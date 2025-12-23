from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Set, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server.data_sources.naver_finance import build_snapshot, fetch_stock_detail


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
app = FastAPI(title="LeadingStock API", version="0.1.0")

# ✅ 반드시 여기서 StaticFiles 등록
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
    return JSONResponse({
        "ok": True,
        "ts": int(time.time()),
        "owner": OWNER_NAME,
    })


# --------------------------------------------------
# Snapshot API
# --------------------------------------------------
@app.get("/snapshot")
async def snapshot(request: Request) -> JSONResponse:
    token = (request.headers.get("X-App-Token") or "").strip()
    if APP_TOKEN and token != APP_TOKEN:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    async with _latest_lock:
        payload = _latest_payload or {
            "type": "empty",
            "ts": int(time.time()),
            "owner": OWNER_NAME,
        }

    return JSONResponse(payload)


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
    if not code.isdigit() or len(code) != 6:
        return JSONResponse({"ok": False, "error": "invalid stock code"}, status_code=400)

    token = (request.headers.get("X-App-Token") or "").strip()
    if APP_TOKEN and token != APP_TOKEN:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    import httpx
    from server.data_sources.naver_finance import RisingStock, ai_opinion_for

    async with httpx.AsyncClient(headers={
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ko-KR,ko;q=0.9"
    }) as client:
        detail = await fetch_stock_detail(client, code)

    if not detail:
        return JSONResponse({"ok": False, "error": "stock not found"}, status_code=404)

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
