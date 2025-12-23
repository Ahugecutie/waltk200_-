from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Set, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import httpx

from server.data_sources.naver_finance import build_snapshot, fetch_stock_detail, RisingStock, ai_opinion_for


# --------------------------------------------------
# Paths & Env
# --------------------------------------------------
APP_ROOT = Path(__file__).resolve().parent.parent
MOBILE_DIR = APP_ROOT / "mobile"

OWNER_NAME = os.environ.get("OWNER_NAME", "김성훈")
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
AUTO_REFRESH_SEC = float(os.environ.get("AUTO_REFRESH_SEC", "60").strip() or "60")


# --------------------------------------------------
# FastAPI App
# --------------------------------------------------
app = FastAPI(
    title="LeadingStock API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

@app.on_event("startup")
async def startup_event():
    import asyncio
    from server.cache_worker import cache_loop
    asyncio.create_task(cache_loop())
    print("[SERVER] Cache worker started")

# ✅ 루트 경로: /app/로 리다이렉트
@app.get("/")
async def root():
    """루트 경로 접근 시 PWA 앱으로 리다이렉트"""
    return RedirectResponse(url="/app/", status_code=302)

# ✅ StaticFiles 마운트 (API 라우트 이후에 배치)
if MOBILE_DIR.exists():
    app.mount(
        "/app",
        StaticFiles(directory=str(MOBILE_DIR), html=True),
        name="mobile"
    )


# --------------------------------------------------
# Global State
# --------------------------------------------------
_latest_payload: Optional[dict] = None
_latest_lock = asyncio.Lock()
_refresh_now = asyncio.Event()
_inflight_refresh: Optional[asyncio.Task] = None


# --------------------------------------------------
# Health
# --------------------------------------------------
@app.get("/health")
def health() -> JSONResponse:
    from server.cache import GLOBAL_CACHE, CACHE_LOCK
    with CACHE_LOCK:
        return JSONResponse({
            "ok": True,
            "ts": int(time.time()),
            "owner": OWNER_NAME,
            "cache_status": GLOBAL_CACHE["status"],
            "updated_at": GLOBAL_CACHE["updated_at"],
            "snapshot_ready": GLOBAL_CACHE["snapshot"] is not None,
            "detail_count": len(GLOBAL_CACHE["detail"]),
        })


# --------------------------------------------------
# Snapshot API
# --------------------------------------------------
@app.get("/snapshot")
async def snapshot(request: Request) -> JSONResponse:
    token = (request.headers.get("X-App-Token") or "").strip()
    if APP_TOKEN and token != APP_TOKEN:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    from server.cache import GLOBAL_CACHE, CACHE_LOCK
    with CACHE_LOCK:
        if GLOBAL_CACHE["snapshot"] is None:
            return JSONResponse({
                "status": "warming_up",
                "message": "데이터 준비 중 (최초 1회)",
                "ts": int(time.time()),
                "owner": OWNER_NAME,
            })
        return JSONResponse(GLOBAL_CACHE["snapshot"])


@app.post("/refresh")
async def refresh(request: Request) -> JSONResponse:
    token = (request.headers.get("X-App-Token") or "").strip()
    if APP_TOKEN and token != APP_TOKEN:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    _refresh_now.set()
    return JSONResponse({"ok": True, "ts": int(time.time())})


# --------------------------------------------------
# Stock Detail
# --------------------------------------------------
@app.get("/stock/{code}")
async def stock_detail(code: str, request: Request) -> JSONResponse:
    # 한국 주식 코드는 일반주 6자리, 우선주 5자리 또는 6자리
    if not code.isdigit() or len(code) < 5 or len(code) > 6:
        return JSONResponse({"ok": False, "error": "invalid stock code"}, status_code=400)

    token = (request.headers.get("X-App-Token") or "").strip()
    if APP_TOKEN and token != APP_TOKEN:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    from server.cache import GLOBAL_CACHE, CACHE_LOCK
    with CACHE_LOCK:
        detail = GLOBAL_CACHE["detail"].get(code)

    if detail is None:
        return JSONResponse({
            "ok": False,
            "status": "not_ready",
            "message": "분석 준비 중",
            "error": "stock_not_ready"
        }, status_code=404)

    # Find corresponding stock from snapshot for signals/ai_opinion
    with CACHE_LOCK:
        snapshot = GLOBAL_CACHE["snapshot"]

    rising_stock = None
    if snapshot and snapshot.get("stocks"):
        for s in snapshot.get("stocks", []):
            if s.get("code") == code:
                rising_stock = RisingStock(
                    code=s["code"],
                    name=s["name"],
                    price=s["price"],
                    change=s.get("change", 0),
                    change_pct=s.get("change_pct", 0.0),
                    volume=s.get("volume", 0),
                    trade_value=s.get("trade_value", 0),
                    market=s.get("market", "KOSPI"),
                )
                break

    if not rising_stock:
        rising_stock = RisingStock(
            code=detail.code,
            name=detail.name,
            price=detail.price,
            change=detail.change,
            change_pct=detail.change_pct,
            volume=detail.volume,
            trade_value=detail.trade_value,
            market=detail.market,
        )

    ai_opinion = ai_opinion_for(rising_stock, detail)

    return JSONResponse({
        "ok": True,
        "data": {
            "code": detail.code,
            "name": detail.name,
            "price": detail.price,
            "change": detail.change,
            "change_pct": detail.change_pct,
            "volume": detail.volume,
            "trade_value": detail.trade_value,
            "market": detail.market,
            "pivot": {
                "pivot": detail.pivot,
                "r1": detail.r1,
                "r2": detail.r2,
                "s1": detail.s1,
                "s2": detail.s2,
            } if detail.pivot else None,
            "news": detail.news or [],
            "financials": detail.financials or [],
            "investor_trends": detail.investor_trends or [],
            "ai_opinion": ai_opinion,
        }
    })


# --------------------------------------------------
# WebSocket Hub
# --------------------------------------------------
class Hub:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket):
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket):
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: dict):
        msg = json.dumps(payload, ensure_ascii=False)
        async with self._lock:
            clients = list(self._clients)

        for ws in clients:
            try:
                await ws.send_text(msg)
            except Exception:
                await self.remove(ws)


hub = Hub()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token = (ws.query_params.get("token") or "").strip()
    if APP_TOKEN and token != APP_TOKEN:
        await ws.close(code=1008)
        return

    await ws.accept()
    await hub.add(ws)

    try:
        await ws.send_text(json.dumps({"type": "hello", "owner": OWNER_NAME}, ensure_ascii=False))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)


# --------------------------------------------------
# Startup: Refresh Loop
# --------------------------------------------------
@app.on_event("startup")
async def startup_event():

    async def do_refresh(seq: int) -> dict:
        global _inflight_refresh

        if _inflight_refresh and not _inflight_refresh.done():
            return await _inflight_refresh

        async def work():
            data = await build_snapshot()
            return {
                "type": "snapshot",
                "seq": seq,
                "ts": int(time.time()),
                "owner": OWNER_NAME,
                "data": data,
            }

        _inflight_refresh = asyncio.create_task(work())
        return await _inflight_refresh

    async def refresh_loop():
        i = 0
        while True:
            i += 1
            try:
                payload = await do_refresh(i)
            except Exception as e:
                payload = {
                    "type": "snapshot",
                    "seq": i,
                    "ts": int(time.time()),
                    "owner": OWNER_NAME,
                    "data": {"error": str(e)},
                }

            async with _latest_lock:
                global _latest_payload
                _latest_payload = payload

            await hub.broadcast(payload)

            try:
                _refresh_now.clear()
                await asyncio.wait_for(_refresh_now.wait(), timeout=AUTO_REFRESH_SEC)
            except asyncio.TimeoutError:
                pass

    asyncio.create_task(refresh_loop())
