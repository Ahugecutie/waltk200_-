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


APP_ROOT = Path(__file__).resolve().parent.parent
MOBILE_DIR = APP_ROOT / "mobile"

OWNER_NAME = os.environ.get("OWNER_NAME", "김성훈")
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
AUTO_REFRESH_SEC = float(os.environ.get("AUTO_REFRESH_SEC", "60").strip() or "60")


app = FastAPI(title="LeadingStock API", version="0.1.0")

_latest_payload: Optional[dict] = None
_latest_lock = asyncio.Lock()
_refresh_now = asyncio.Event()
_inflight_refresh: Optional[asyncio.Task] = None


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "ts": int(time.time()),
            "owner": OWNER_NAME,
        }
    )

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
    # Triggers an immediate refresh cycle (best-effort).
    _refresh_now.set()
    return JSONResponse({"ok": True, "ts": int(time.time())})


@app.get("/stock/{code}")
async def stock_detail(code: str, request: Request) -> JSONResponse:
    """
    Get detailed information for a specific stock.
    Includes: pivot points, news, financials, investor trends.
    """
    token = (request.headers.get("X-App-Token") or "").strip()
    if APP_TOKEN and token != APP_TOKEN:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    
    import httpx
    async with httpx.AsyncClient(headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9"
    }) as client:
        detail = await fetch_stock_detail(client, code)
    
    if not detail:
        return JSONResponse({"ok": False, "error": "stock not found"}, status_code=404)
    
    # Convert to dict for JSON response
    result = {
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
    }
    
    return JSONResponse({"ok": True, "data": result})


class Hub:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        msg = json.dumps(payload, ensure_ascii=False)
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


hub = Hub()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    token = (ws.query_params.get("token") or "").strip()
    if APP_TOKEN and token != APP_TOKEN:
        # Policy violation
        await ws.close(code=1008)
        return
    await ws.accept()
    await hub.add(ws)
    try:
        # Initial hello payload
        await ws.send_text(json.dumps({"type": "hello", "owner": OWNER_NAME}, ensure_ascii=False))
        while True:
            # Keep connection alive. We don't require client messages.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)


@app.on_event("startup")
async def _startup() -> None:
    # Serve PWA
    if MOBILE_DIR.exists():
        app.mount("/", StaticFiles(directory=str(MOBILE_DIR), html=True), name="mobile")

    async def do_refresh(seq: int) -> dict:
        # Guard against concurrent refresh storms
        global _inflight_refresh
        if _inflight_refresh and not _inflight_refresh.done():
            try:
                return await asyncio.wait_for(_inflight_refresh, timeout=20.0)
            except Exception:
                pass

        async def _work() -> dict:
            data = await build_snapshot()
            return {
                "type": "snapshot",
                "seq": seq,
                "ts": int(time.time()),
                "owner": OWNER_NAME,
                "data": data,
            }

        _inflight_refresh = asyncio.create_task(_work())
        return await asyncio.wait_for(_inflight_refresh, timeout=25.0)

    # Refresh loop (real snapshot). Manual refresh triggers immediate recalculation.
    async def refresh_loop() -> None:
        i = 0
        while True:
            i += 1
            try:
                payload = await do_refresh(i)
            except Exception as e:
                # Keep last payload if data source fails, but still update heartbeat
                payload = {
                    "type": "snapshot",
                    "seq": i,
                    "ts": int(time.time()),
                    "owner": OWNER_NAME,
                    "data": {
                        "indices": [],
                        "themes": [],
                        "stocks": [],
                        "source": "error",
                        "error": str(e),
                    },
                }

            async with _latest_lock:
                global _latest_payload
                _latest_payload = payload
            # Optional push to connected clients
            await hub.broadcast(payload)

            # Wait for either periodic refresh or manual refresh trigger
            try:
                _refresh_now.clear()
                await asyncio.wait_for(_refresh_now.wait(), timeout=max(5.0, AUTO_REFRESH_SEC))
            except asyncio.TimeoutError:
                pass

    asyncio.create_task(refresh_loop())


