from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class IndexQuote:
    name: str
    value: float
    change: float
    change_pct: float


@dataclass(frozen=True)
class RisingStock:
    code: str
    name: str
    price: int
    change: int
    change_pct: float
    volume: int
    trade_value: int  # KRW
    market: str  # "KOSPI" | "KOSDAQ"


def _to_int(s: str) -> int:
    """
    Extract the first integer-like token from a mixed string.
    Examples:
      '상한가 3,520' -> 3520
      '+1,234' -> 1234
      '-' / 'N/A' -> 0
    """
    s = (s or "").strip()
    if s in ("", "-", "N/A"):
        return 0
    m = re.search(r"[-+]?\d[\d,]*", s)
    if not m:
        return 0
    return int(m.group(0).replace(",", "").replace("+", ""))


def _to_float(s: str) -> float:
    """
    Extract the first float-like token from a mixed string.
    Examples:
      '+29.98%' -> 29.98
      '전일비 1,234' -> 1234.0
    """
    s = (s or "").strip()
    if s in ("", "-", "N/A"):
        return 0.0
    s = s.replace("%", "")
    m = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return 0.0
    return float(m.group(0).replace(",", "").replace("+", ""))


async def _get(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, follow_redirects=True, timeout=15.0)
    r.raise_for_status()
    r.encoding = "euc-kr"  # Naver finance still commonly uses EUC-KR
    return r.text


async def fetch_index_quotes(client: httpx.AsyncClient) -> List[IndexQuote]:
    """
    Best-effort parsing for KOSPI/KOSDAQ from `sise_index.naver`.
    This is more stable than the main page and works in headless environments.
    """
    async def fetch_one(code: str) -> Optional[IndexQuote]:
        try:
            html = await _get(client, f"https://finance.naver.com/sise/sise_index.naver?code={code}")
            soup = BeautifulSoup(html, "html.parser")
            now_el = soup.select_one("em#now_value")
            fluc_el = soup.select_one("#change_value_and_rate")
            quo_el = soup.select_one("div#quotient")

            if not now_el:
                return None
            now = _to_float(now_el.get_text(strip=True))

            fluc_txt = fluc_el.get_text(" ", strip=True) if fluc_el else ""
            # Example:
            #  - "13.76 +0.34% 전일대비"
            #  - "9.19 -0.99% 전일대비"
            nums = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", fluc_txt.replace("%", ""))
            ch = float(nums[0].replace(",", "").replace("+", "")) if len(nums) >= 1 else 0.0
            pct = float(nums[1].replace(",", "").replace("+", "")) if len(nums) >= 2 else 0.0

            # Determine sign via quotient class if available (KOSDAQ uses 'dn')
            cls = quo_el.get("class", []) if quo_el else []
            if "dn" in cls or "down" in cls:
                ch = -abs(ch)
                pct = -abs(pct)
            elif "up" in cls:
                ch = abs(ch)
                pct = abs(pct)
            # else: keep sign from parsed string

            return IndexQuote(code, now, ch, pct)
        except Exception:
            return None

    out: List[IndexQuote] = []
    for code in ("KOSPI", "KOSDAQ"):
        q = await fetch_one(code)
        if q:
            out.append(q)
    return out


async def fetch_rising_stocks(client: httpx.AsyncClient, market: str, limit: int = 50) -> List[RisingStock]:
    """
    Scrape Naver '상승' list.
    market: "KOSPI" -> sosok=0, "KOSDAQ" -> sosok=1
    """
    sosok = "0" if market.upper() == "KOSPI" else "1"
    html = await _get(client, f"https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.type_2")
    if not table:
        return []

    rows = table.select("tr")
    out: List[RisingStock] = []
    for tr in rows:
        tds = tr.find_all("td")
        # Expected layout (type_2 / sise_rise):
        # 0 rank, 1 name, 2 price, 3 change, 4 change%, 5 volume, 6 buy, 7 sell, 8 trade_value, 9 etc...
        if len(tds) < 9:
            continue
        a = tr.select_one("a.tltle")
        if not a:
            continue
        name = a.get_text(strip=True)
        href = a.get("href", "")
        m = re.search(r"code=(\d+)", href)
        if not m:
            continue
        code = m.group(1)

        # Robust parsing: rely on known column positions and regex-based numeric extraction.
        price = _to_int(tds[2].get_text(" ", strip=True))
        change = _to_int(tds[3].get_text(" ", strip=True))
        change_pct = _to_float(tds[4].get_text(" ", strip=True))
        volume = _to_int(tds[5].get_text(" ", strip=True))
        trade_value = _to_int(tds[8].get_text(" ", strip=True)) if len(tds) > 8 else 0

        out.append(
            RisingStock(
                code=code,
                name=name,
                price=price,
                change=change,
                change_pct=change_pct,
                volume=volume,
                trade_value=trade_value,
                market=market.upper(),
            )
        )
        if len(out) >= limit:
            break
    return out


async def build_snapshot() -> dict:
    async with httpx.AsyncClient(headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}) as client:
        indices = await fetch_index_quotes(client)
        kospi_rise = await fetch_rising_stocks(client, "KOSPI", limit=80)
        kosdaq_rise = await fetch_rising_stocks(client, "KOSDAQ", limit=80)
        merged = kospi_rise + kosdaq_rise
        merged.sort(key=lambda x: x.change_pct, reverse=True)
        top20 = merged[:20]

    return {
        "indices": [
            {"name": q.name, "value": q.value, "change": q.change, "change_pct": q.change_pct} for q in indices
        ],
        "themes": [],  # TODO: fill later (original exe logic)
        "stocks": [
            {
                "code": s.code,
                "name": s.name,
                "market": s.market,
                "price": s.price,
                "change": s.change,
                "change_pct": s.change_pct,
                "volume": s.volume,
                "trade_value": s.trade_value,
                "link": f"https://finance.naver.com/item/main.naver?code={s.code}",
                "score": int(min(150, max(0, round(s.change_pct * 5)))),  # placeholder scoring
            }
            for s in top20
        ],
        "source": "naver_finance",
    }


