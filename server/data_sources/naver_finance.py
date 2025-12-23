from __future__ import annotations

import re
from collections import Counter
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


def calculate_score(stock: RisingStock) -> int:
    """
    Calculate stock score based on multiple factors.
    This will be refined to match original EXE logic exactly.
    
    Factors considered:
    - Change percentage (primary)
    - Trade value (liquidity)
    - Volume (participation)
    - Market (KOSPI vs KOSDAQ)
    """
    base_score = stock.change_pct * 5  # Base: 5 points per 1% change
    
    # Trade value bonus (higher liquidity = higher score)
    if stock.trade_value >= 500000:  # 50억 이상
        base_score += 20
    elif stock.trade_value >= 200000:  # 20억 이상
        base_score += 10
    elif stock.trade_value >= 100000:  # 10억 이상
        base_score += 5
    
    # Volume bonus (high participation)
    if stock.volume >= 50000000:  # 5천만주 이상
        base_score += 15
    elif stock.volume >= 20000000:  # 2천만주 이상
        base_score += 8
    elif stock.volume >= 10000000:  # 1천만주 이상
        base_score += 3
    
    # Market bonus (KOSDAQ tends to be more volatile)
    if stock.market == "KOSDAQ":
        base_score += 2
    
    # Limit-up bonus
    if stock.change_pct >= 29.8:
        base_score += 10
    
    # Cap at 150 (as seen in original)
    return int(min(150, max(0, round(base_score))))


def detect_themes(stocks: List[RisingStock]) -> List[dict]:
    """
    Detect leading themes from stock names and group by common keywords.
    This is a heuristic approach - will be refined based on original EXE logic.
    """
    from collections import Counter
    
    # Common theme keywords in Korean stock market
    theme_keywords = {
        "반도체": ["반도체", "칩", "웨이퍼", "실리콘"],
        "배터리": ["배터리", "전지", "리튬", "에너지"],
        "바이오": ["바이오", "제약", "의료", "바이오텍", "제약바이오"],
        "AI": ["AI", "인공지능", "머신러닝", "딥러닝"],
        "전기차": ["전기차", "전기", "EV", "전동차"],
        "2차전지": ["2차전지", "이차전지", "배터리"],
        "게임": ["게임", "엔터테인먼트"],
        "증권": ["증권", "투자", "금융"],
        "건설": ["건설", "시공", "토목"],
        "화학": ["화학", "석유화학"],
        "철강": ["철강", "제철"],
        "IT": ["IT", "소프트웨어", "시스템"],
    }
    
    # Count theme occurrences in top stocks
    theme_counts: Counter[str] = Counter()
    theme_stocks: dict[str, List[RisingStock]] = {}
    
    for stock in stocks:
        name = stock.name
        matched_themes = []
        
        for theme, keywords in theme_keywords.items():
            if any(kw in name for kw in keywords):
                matched_themes.append(theme)
                if theme not in theme_stocks:
                    theme_stocks[theme] = []
                theme_stocks[theme].append(stock)
        
        # If no theme matched, check for common suffixes
        if not matched_themes:
            if name.endswith("증권") or name.endswith("증권우"):
                theme_counts["증권"] += 1
                if "증권" not in theme_stocks:
                    theme_stocks["증권"] = []
                theme_stocks["증권"].append(stock)
    
    # Calculate theme scores (weighted by stock performance)
    theme_scores: list[tuple[str, float, int]] = []
    for theme, theme_stock_list in theme_stocks.items():
        if len(theme_stock_list) >= 2:  # At least 2 stocks to form a theme
            avg_change = sum(s.change_pct for s in theme_stock_list) / len(theme_stock_list)
            total_trade_value = sum(s.trade_value for s in theme_stock_list)
            # Score = (number of stocks) * (avg change %) * (log of total trade value)
            score = len(theme_stock_list) * avg_change * (1 + (total_trade_value / 1000000) ** 0.3)
            theme_scores.append((theme, score, len(theme_stock_list)))
    
    # Sort by score and return top 5
    theme_scores.sort(key=lambda x: x[1], reverse=True)
    
    return [
        {
            "name": theme,
            "count": count,
            "score": round(score, 2),
        }
        for theme, score, count in theme_scores[:5]
    ]


async def build_snapshot() -> dict:
    async with httpx.AsyncClient(headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}) as client:
        indices = await fetch_index_quotes(client)
        kospi_rise = await fetch_rising_stocks(client, "KOSPI", limit=80)
        kosdaq_rise = await fetch_rising_stocks(client, "KOSDAQ", limit=80)
        merged = kospi_rise + kosdaq_rise
        merged.sort(key=lambda x: x.change_pct, reverse=True)
        top20 = merged[:20]
        
        # Detect themes from all rising stocks (not just top20)
        all_rising = kospi_rise + kosdaq_rise
        themes = detect_themes(all_rising)

    def signals_for(s: RisingStock) -> list[dict]:
        sigs: list[dict] = []
        # Heuristic signals (aligned with EXE analyzer logic)
        if s.change_pct >= 29.8:
            sigs.append({"title": "🔒 상한가 홀딩 / 매수 금지", "desc": "상한가", "tone": "bad"})
            # 상한가일 때도 거래대금/모멘텀에 따라 돌파매매 신호 추가
            if s.trade_value >= 200000:
                sigs.append({"title": f"⚡ 돌파 매매 (손절 {int(s.price * 0.93):,}원)", "desc": "급등, 거래대금 폭발", "tone": "warn"})
            else:
                sigs.append({"title": f"⚡ 돌파 매매 (손절 {int(s.price * 0.93):,}원)", "desc": "급등, 모멘텀 수급", "tone": "warn"})
        elif s.change_pct >= 20:
            if s.trade_value >= 200000:
                sigs.append({"title": f"⚡ 돌파 매매 (손절 {int(s.price * 0.95):,}원)", "desc": "급등, 거래대금 폭발", "tone": "warn"})
            else:
                sigs.append({"title": f"⚡ 돌파 매매 (손절 {int(s.price * 0.95):,}원)", "desc": "급등, 모멘텀 수급", "tone": "warn"})
        elif s.change_pct >= 12:
            sigs.append({"title": "🧲 눌림목 매수 (분할 진입)", "desc": "강세, 거래대금 확인", "tone": "ok"})
        else:
            sigs.append({"title": "👀 고가 놀이 (수급 확인)", "desc": "강세, 변동성 유의", "tone": "neutral"})

        if s.volume >= 20000000:
            sigs.append({"title": "📈 거래량 급증", "desc": "수급 변동성 확대", "tone": "neutral"})
        return sigs[:6]

    def ai_opinion_for(s: RisingStock) -> str:
        # Lightweight rule-based placeholder (no external AI calls)
        if s.change_pct >= 29.8:
            return "상한가 구간입니다. 추격매수는 위험하며, 보유자는 변동성에 대비해 분할 청산/손절 기준을 명확히 하세요."
        if s.change_pct >= 20:
            return "급등 구간입니다. 거래대금과 추가 수급 유입을 확인하면서, 손절 라인을 먼저 정하는 것이 좋습니다."
        if s.change_pct >= 12:
            return "강세 흐름입니다. 눌림 구간에서 분할 진입을 고려하되, 거래대금이 유지되는지 확인하세요."
        return "단기 변동성이 낮은 편입니다. 뉴스/수급 변화를 확인하며 보수적으로 접근하세요."

    return {
        "indices": [
            {"name": q.name, "value": q.value, "change": q.change, "change_pct": q.change_pct} for q in indices
        ],
        "themes": themes,
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
                "score": calculate_score(s),  # Improved scoring based on multiple factors
                "signals": signals_for(s),
                "ai_opinion": ai_opinion_for(s),
            }
            for s in top20
        ],
        "source": "naver_finance",
    }


