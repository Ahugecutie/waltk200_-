import os
import asyncio
import traceback
import httpx
from time import time
from server.cache import GLOBAL_CACHE, CACHE_LOCK
from server.data_sources.naver_finance import (
    build_snapshot,
    fetch_stock_detail,
)

CACHE_INTERVAL = int(os.getenv("CACHE_INTERVAL", "60"))
MAX_BACKOFF = 300  # seconds

async def cache_loop():
    backoff = 1

    while True:
        try:
            print("[CACHE] update started")

            # 🔹 AsyncClient 1회 생성 (재사용)
            from server.data_sources.naver_finance import UA
            async with httpx.AsyncClient(
                headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
                timeout=15.0
            ) as client:
                new_snapshot = await build_snapshot(client)

                new_detail = {}
                for stock in new_snapshot.get("stocks", []):
                    code = stock.get("code")
                    if not code:
                        continue

                    try:
                        detail = await fetch_stock_detail(client, code)
                        if detail:
                            new_detail[code] = detail
                    except (
                        httpx.ReadTimeout,
                        httpx.ConnectTimeout,
                        httpx.HTTPError,
                    ) as e:
                        print(f"[CACHE WARN] detail failed: {code} ({e})")
                        continue

            # 🔹 swap (atomic)
            with CACHE_LOCK:
                GLOBAL_CACHE["snapshot"] = new_snapshot
                GLOBAL_CACHE["detail"] = new_detail
                GLOBAL_CACHE["updated_at"] = time()
                GLOBAL_CACHE["status"] = "ready"

            print(f"[CACHE] update completed ({len(new_detail)} stocks)")
            backoff = 1
            await asyncio.sleep(CACHE_INTERVAL)

        except Exception as e:
            print("[CACHE ERROR]", e)
            traceback.print_exc()

            with CACHE_LOCK:
                GLOBAL_CACHE["status"] = "error"

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

