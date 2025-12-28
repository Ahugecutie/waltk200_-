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


@dataclass
class StockDetail:
    code: str
    name: str
    price: int
    change: int
    change_pct: float
    volume: int
    trade_value: int
    market: str
    # Pivot data
    pivot: Optional[float] = None
    r1: Optional[float] = None  # 1차 저항
    r2: Optional[float] = None  # 2차 저항
    s1: Optional[float] = None  # 1차 지지
    s2: Optional[float] = None  # 2차 지지
    # Previous day data for pivot calculation
    prev_high: Optional[float] = None
    prev_low: Optional[float] = None
    prev_close: Optional[float] = None
    # News
    news: Optional[List[dict]] = None  # [{"title": str, "date": str, "url": str}]
    # Financial summary - date-keyed dictionary structure
    financials: Optional[dict] = None  # {"2024.12": {"sales": float, "operating_profit": float}, ...}
    # Investor trends
    investor_trends: Optional[List[dict]] = None  # [{"date": str, "institution": int, "foreigner": int, "foreigner_shares": int, "foreigner_ratio": float}]


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
    # Try to detect encoding, fallback to euc-kr
    if r.encoding is None or r.encoding.lower() in ('iso-8859-1', 'windows-1252'):
        r.encoding = "euc-kr"  # Naver finance commonly uses EUC-KR
    # Ensure proper encoding for Korean text
    try:
        text = r.text
        # Verify encoding by trying to encode/decode
        text.encode('utf-8')
        return text
    except (UnicodeEncodeError, UnicodeDecodeError):
        # If encoding fails, try to fix it
        r.encoding = "euc-kr"
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

    # 헤더에서 컬럼 인덱스 찾기
    thead = table.select_one("thead")
    header_map = {}
    if thead:
        headers = thead.select("th")
        for i, th in enumerate(headers):
            header_text = th.get_text(strip=True)
            if "종목명" in header_text or "종목" in header_text:
                header_map["name"] = i
            elif "현재가" in header_text or "종가" in header_text:
                header_map["price"] = i
            elif "전일비" in header_text or "등락" in header_text:
                header_map["change"] = i
            elif "등락률" in header_text or "%" in header_text:
                header_map["change_pct"] = i
            elif "거래량" in header_text:
                header_map["volume"] = i
            elif "거래대금" in header_text:
                header_map["trade_value"] = i
    
    rows = table.select("tr")
    out: List[RisingStock] = []
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 5:
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

        # Filter out ETN, ETF, and special stocks (스펙종목)
        name_upper = name.upper()
        if any(keyword in name_upper for keyword in ["ETN", "ETF", "스펙", "스팩", "SPAC"]):
            continue

        # 헤더 매칭으로 정확한 컬럼 찾기, 없으면 기본 인덱스 사용
        price_idx = header_map.get("price", 2)
        change_idx = header_map.get("change", 3)
        change_pct_idx = header_map.get("change_pct", 4)
        volume_idx = header_map.get("volume", 5)
        trade_value_idx = header_map.get("trade_value", 8)
        
        price = _to_int(tds[price_idx].get_text(" ", strip=True)) if price_idx < len(tds) else 0
        change = _to_int(tds[change_idx].get_text(" ", strip=True)) if change_idx < len(tds) else 0
        change_pct = _to_float(tds[change_pct_idx].get_text(" ", strip=True)) if change_pct_idx < len(tds) else 0.0
        volume = _to_int(tds[volume_idx].get_text(" ", strip=True)) if volume_idx < len(tds) else 0
        # 거래대금은 백만원 단위로 표시되므로 원 단위로 변환
        trade_value_raw = tds[trade_value_idx].get_text(" ", strip=True) if trade_value_idx < len(tds) else "0"
        trade_value = _to_int(trade_value_raw) * 1_000_000  # 백만원 → 원

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


def signals_for(s: RisingStock) -> list[dict]:
    """
    Generate trading signals based on stock performance.
    Comprehensive signal patterns matching original EXE logic.
    
    Signal Patterns:
    1. 🔒 상한가 홀딩 / 매수 금지 - 상한가(29.8%+) 구간
    2. ⚡ 돌파 매매 - 강한 상승세(20%+) 돌파 구간
    3. 🧲 눌림목 매수 - 조정 후 재상승 기회(12%+)
    4. 👀 고가 놀이 - 보합세, 수급 확인 필요(5-12%)
    5. 📊 추세 추종 - 안정적 상승 추세(5% 미만)
    6. 💰 차익 실현 매물 출회(관망) - 고가대 거래량 증가, 조정 가능성
    7. 📈 거래량 급증 - 거래량 폭증 신호
    """
    sigs: list[dict] = []
    
    # Limit-up detection (상한가)
    if s.change_pct >= 29.8:
        sigs.append({"title": "🔒 상한가 홀딩 / 매수 금지", "desc": "상한가", "tone": "bad"})
        # Calculate stop-loss (7% below current price for limit-up)
        stop_loss = int(s.price * 0.93)
        if s.trade_value >= 200000:  # 20억 이상 = 거래대금 폭발
            sigs.append({"title": f"⚡ 돌파 매매 (손절 {stop_loss:,}원)", "desc": "급등, 거래대금 폭발", "tone": "warn"})
        else:
            sigs.append({"title": f"⚡ 돌파 매매 (손절 {stop_loss:,}원)", "desc": "급등, 모멘텀 수급", "tone": "warn"})
    
    # Strong breakout (20%+ but not limit-up)
    elif s.change_pct >= 20:
        stop_loss = int(s.price * 0.95)  # 5% stop-loss for strong moves
        if s.trade_value >= 200000:  # 20억 이상
            sigs.append({"title": f"⚡ 돌파 매매 (손절 {stop_loss:,}원)", "desc": "급등, 거래대금 폭발", "tone": "warn"})
        else:
            sigs.append({"title": f"⚡ 돌파 매매 (손절 {stop_loss:,}원)", "desc": "급등, 모멘텀 수급", "tone": "warn"})
    
    # Pullback entry opportunity (12%+)
    elif s.change_pct >= 12:
        sigs.append({"title": "🧲 눌림목 매수 (분할 진입)", "desc": "강세, 거래대금 확인", "tone": "ok"})
    
    # Moderate strength (5-12%)
    elif s.change_pct >= 5:
        # Check for profit-taking signals (high volume at high price)
        if s.volume >= 15000000 and s.trade_value >= 150000:  # 고가대 거래량 증가
            sigs.append({"title": "💰 차익 실현 매물 출회(관망)", "desc": "고가대 거래량 증가, 조정 가능성", "tone": "neutral"})
        else:
            sigs.append({"title": "👀 고가 놀이 (수급 확인)", "desc": "강세, 변동성 유의", "tone": "neutral"})
    
    # Stable uptrend (0-5%)
    elif s.change_pct > 0:
        if s.volume >= 10000000 and s.trade_value >= 100000:  # 안정적 상승 추세
            sigs.append({"title": "📊 추세 추종", "desc": "안정적 상승 추세, 지속 모니터링", "tone": "ok"})
        else:
            sigs.append({"title": "👀 고가 놀이 (수급 확인)", "desc": "보합세, 수급 확인 필요", "tone": "neutral"})
    
    # Negative or flat
    else:
        sigs.append({"title": "👀 고가 놀이 (수급 확인)", "desc": "보합세, 수급 확인 필요", "tone": "neutral"})

    # Volume surge indicator (applies to all cases)
    if s.volume >= 20000000:  # 2천만주 이상
        sigs.append({"title": "📈 거래량 급증", "desc": "수급 변동성 확대", "tone": "neutral"})
    
    return sigs[:6]


def ai_opinion_for(s: RisingStock, detail: Optional[StockDetail] = None) -> str:
        """
        Generate comprehensive AI investment opinion based on multiple factors.
        Enhanced with detailed news analysis, technical indicators, financials, and investor trends.
        """
        parts = []
        
        # === 1. Market Context & Overall Assessment ===
        market_context = []
        if s.change_pct >= 29.8:
            market_context.append("**상한가 구간**")
        elif s.change_pct >= 20:
            market_context.append("**급등 구간**")
        elif s.change_pct >= 12:
            market_context.append("**강세 흐름**")
        elif s.change_pct >= 5:
            market_context.append("**중간 강세**")
        else:
            market_context.append("**보합세**")
        
        if s.trade_value >= 500000:  # 50억 이상
            market_context.append("거래대금이 **폭발적으로 증가**")
        elif s.trade_value >= 200000:  # 20억 이상
            market_context.append("거래대금이 **크게 증가**")
        elif s.trade_value >= 100000:  # 10억 이상
            market_context.append("거래대금이 **활발**")
        
        if s.volume >= 50000000:  # 5천만주 이상
            market_context.append("거래량이 **폭증**")
        elif s.volume >= 20000000:  # 2천만주 이상
            market_context.append("거래량이 **대폭 증가**")
        elif s.volume >= 10000000:  # 1천만주 이상
            market_context.append("거래량이 **활발**")
        
        if market_context:
            parts.append(f"현재 {', '.join(market_context)}한 상태입니다.")
        
        # === 2. Investor Trend Analysis (Detailed) ===
        if detail and detail.investor_trends and len(detail.investor_trends) > 0:
            latest = detail.investor_trends[0]
            if isinstance(latest, dict):
                foreigner_val = latest.get("foreigner", 0)
                institution_val = latest.get("institution", 0)
            else:
                foreigner_val = getattr(latest, "foreigner", 0) if hasattr(latest, "foreigner") else 0
                institution_val = getattr(latest, "institution", 0) if hasattr(latest, "institution") else 0
            
            investor_analysis = []
            if foreigner_val > 200000:  # 외국인 순매수 2억 이상
                investor_analysis.append("**외국인이 강력한 매수세**를 보이고 있어")
            elif foreigner_val > 100000:  # 외국인 순매수 1억 이상
                investor_analysis.append("**외국인이 매수세를 주도**하고 있어")
            elif foreigner_val < -200000:  # 외국인 순매도 2억 이상
                investor_analysis.append("**외국인 매도세가 강하게 지속**되어")
            elif foreigner_val < -100000:  # 외국인 순매도 1억 이상
                investor_analysis.append("외국인 매도세가 지속되어")
            
            if institution_val > 200000:  # 기관 순매수 2억 이상
                investor_analysis.append("**기관도 대규모 매수**에 나서")
            elif institution_val > 100000:  # 기관 순매수 1억 이상
                investor_analysis.append("기관도 매수세를 보이며")
            elif institution_val < -200000:  # 기관 순매도 2억 이상
                investor_analysis.append("**기관이 대규모 매도**에 나서")
            elif institution_val < -100000:  # 기관 순매도 1억 이상
                investor_analysis.append("기관 매도세가 지속되어")
            
            if investor_analysis:
                parts.append(" ".join(investor_analysis) + " 주가 움직임에 영향을 미치고 있습니다.")
        
        # === 3. News Analysis (Enhanced) ===
        if detail and detail.news and len(detail.news) > 0:
            news_list = detail.news if isinstance(detail.news, list) else []
            news_titles = " ".join([(n.get("title", "") if isinstance(n, dict) else str(n)) for n in news_list[:5]])
            news_text = news_titles.lower()
            
            # 긍정적 키워드
            positive_keywords = ["인기 검색", "검색 종목", "급등", "상한가", "구조대", "왔다", "상승", "호재", 
                               "실적", "수주", "계약", "승인", "인허가", "신약", "개발", "성공", "돌파"]
            # 부정적 키워드
            negative_keywords = ["하락", "급락", "부진", "실적", "적자", "손실", "경고", "주의", "리콜", "조사"]
            
            positive_count = sum(1 for kw in positive_keywords if kw in news_text)
            negative_count = sum(1 for kw in negative_keywords if kw in news_text)
            
            if "인기 검색" in news_titles or "검색 종목" in news_titles:
                parts.append("주가 상승의 주요 트리거는 **'인기 검색 종목'** 관련 이슈로 판단되며, 단기 모멘텀이 강합니다.")
            elif positive_count >= 2:
                parts.append("최근 뉴스에서 **긍정적 이슈가 다수 확인**되어 주가에 호재로 작용하고 있습니다.")
            elif negative_count >= 2:
                parts.append("최근 뉴스에서 **부정적 이슈가 확인**되어 주의가 필요합니다.")
            elif positive_count > 0:
                parts.append("최근 뉴스 이슈가 주가에 **긍정적 영향을 미치고 있습니다**.")
            elif len(news_list) > 0:
                parts.append("뉴스 이슈를 지속적으로 모니터링하시기 바랍니다.")
        
        # === 4. Technical Analysis (Comprehensive) ===
        if detail and detail.pivot and detail.price:
            current = detail.price
            pivot = detail.pivot
            
            # Pivot position analysis
            if detail.r2 and current >= detail.r2 * 0.98:
                parts.append("기술적으로 **2차 저항선(R2)을 돌파**한 매우 강한 상승 구간입니다.")
                if s.change_pct >= 20:
                    parts.append("단기 과열 구간에 진입했으므로 **추격매수는 위험**하며, 보유자는 분할 청산을 고려하세요.")
                else:
                    parts.append("추가 상승 여력이 있을 수 있으나, **변동성과 조정 가능성**에 주의하세요.")
            elif detail.r1 and current >= detail.r1 * 0.98:
                parts.append("**1차 저항선(R1) 근처**에서 저항을 받을 수 있으며, 돌파 여부가 관건입니다.")
                if s.trade_value >= 200000:
                    parts.append("거래대금이 충분하여 돌파 가능성이 있으나, 실패 시 조정 가능성도 있습니다.")
            elif current >= pivot * 0.98:
                parts.append("**Pivot Point 근처**에서 움직이고 있으며, 방향성 확립이 필요합니다.")
            elif detail.s1 and current <= detail.s1 * 1.02:
                parts.append("**1차 지지선(S1) 근처**에서 지지를 받고 있어 하락 방어력이 있습니다.")
                if s.change_pct > 0:
                    parts.append("지지선에서 반등할 경우 추가 상승 여력이 있을 수 있습니다.")
            elif detail.s2 and current <= detail.s2 * 1.02:
                parts.append("**2차 지지선(S2) 근처**에 위치하여 강한 지지대 역할을 하고 있습니다.")
        
        # === 5. Financial Analysis ===
        if detail and detail.financials and len(detail.financials) > 0:
            financial_list = detail.financials if isinstance(detail.financials, list) else []
            if len(financial_list) > 0:
                latest_fin = financial_list[0]
                if isinstance(latest_fin, dict):
                    sales = latest_fin.get("sales", 0)
                    operating_profit = latest_fin.get("operating_profit", 0)
                else:
                    sales = getattr(latest_fin, "sales", 0) if hasattr(latest_fin, "sales") else 0
                    operating_profit = getattr(latest_fin, "operating_profit", 0) if hasattr(latest_fin, "operating_profit") else 0
                
                if operating_profit > 0 and sales > 0:
                    margin = (operating_profit / sales) * 100
                    if margin >= 20:
                        parts.append("**재무 건전성이 우수**하며 영업이익률이 높아 안정적인 기업입니다.")
                    elif margin >= 10:
                        parts.append("재무 상태가 양호하며 수익성이 안정적입니다.")
                    elif margin < 0:
                        parts.append("**재무 상태에 주의**가 필요하며, 실적 개선 여부를 지속 모니터링하세요.")
        
        # === 6. Risk Assessment & Trading Strategy ===
        risk_parts = []
        
        if s.change_pct >= 29.8:
            risk_parts.append("**상한가 구간**이므로 추격매수는 매우 위험합니다.")
            if s.trade_value >= 200000:
                risk_parts.append("거래대금이 폭발적으로 증가했으나, 이는 과열 신호일 수 있습니다.")
            risk_parts.append("보유자는 **변동성에 대비해 분할 청산/손절 기준을 명확히** 하시기 바랍니다.")
        elif s.change_pct >= 20:
            risk_parts.append("**급등 구간**이므로 추가 수급 유입을 확인하면서 **손절 라인을 먼저 정하는 것**이 중요합니다.")
            if s.trade_value >= 200000:
                risk_parts.append("거래대금이 크게 증가하여 모멘텀이 강하지만, 조정 가능성도 있습니다.")
        elif s.change_pct >= 12:
            risk_parts.append("**강세 흐름**이 지속되고 있습니다.")
            if s.volume >= 10000000:
                risk_parts.append("거래량이 활발하여 유동성이 좋습니다.")
            risk_parts.append("**눌림 구간에서 분할 진입**을 고려하되, 거래대금이 유지되는지 확인하세요.")
        elif s.change_pct >= 5:
            risk_parts.append("**중간 강세** 구간입니다.")
            risk_parts.append("뉴스와 수급 변화를 지속적으로 모니터링하며, **추세가 지속되는지 확인**하세요.")
        else:
            risk_parts.append("**단기 변동성이 낮은 편**입니다.")
            risk_parts.append("뉴스/수급 변화를 확인하며 **보수적으로 접근**하시기 바랍니다.")
        
        if risk_parts:
            parts.append(" ".join(risk_parts))
        
        # === 7. Market-Specific Considerations ===
        if s.market == "KOSDAQ":
            if s.change_pct >= 20:
                parts.append("코스닥 특성상 **변동성이 크므로 리스크 관리**가 특히 중요합니다.")
        elif s.market == "KOSPI":
            if s.change_pct >= 20:
                parts.append("코스피 대형주 특성상 **안정성은 높으나 상승 모멘텀 지속 여부**를 확인하세요.")
        
        # === Final Summary ===
        if not parts:
            return "분석 중..."
        
        return " ".join(parts)


async def build_snapshot(client: httpx.AsyncClient) -> dict:
    indices = await fetch_index_quotes(client)
    kospi_rise = await fetch_rising_stocks(client, "KOSPI", limit=80)
    kosdaq_rise = await fetch_rising_stocks(client, "KOSDAQ", limit=80)
    merged = kospi_rise + kosdaq_rise
    # Sort by Score (not just change_pct) to consider liquidity and participation
    merged.sort(key=lambda x: calculate_score(x), reverse=True)
    top30 = merged[:30]
    
    # Detect themes from all rising stocks (not just top30)
    all_rising = kospi_rise + kosdaq_rise
    themes = detect_themes(all_rising)

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
                "ai_opinion": ai_opinion_for(s, None),  # Basic opinion, will be enhanced with detail in modal
            }
            for s in top30
        ],
        "source": "naver_finance",
    }


def calculate_pivot_points(high: float, low: float, close: float) -> dict:
    """
    Calculate Pivot Point and support/resistance levels.
    Standard pivot point formula:
    - Pivot = (High + Low + Close) / 3
    - R1 = 2 * Pivot - Low
    - R2 = Pivot + (High - Low)
    - S1 = 2 * Pivot - High
    - S2 = Pivot - (High - Low)
    """
    pivot = (high + low + close) / 3
    r1 = 2 * pivot - low
    r2 = pivot + (high - low)
    s1 = 2 * pivot - high
    s2 = pivot - (high - low)
    return {
        "pivot": round(pivot, 0),
        "r1": round(r1, 0),
        "r2": round(r2, 0),
        "s1": round(s1, 0),
        "s2": round(s2, 0),
    }


async def fetch_stock_detail(client: httpx.AsyncClient, code: str) -> Optional[StockDetail]:
    """
    Fetch detailed information for a specific stock from Naver Finance.
    Includes: pivot points, news, financials, investor trends.
    """
    try:
        # Main stock page
        html = await _get(client, f"https://finance.naver.com/item/main.naver?code={code}")
        soup = BeautifulSoup(html, "html.parser")
        
        # Debug: log if page loaded
        if not soup:
            print(f"Warning: Failed to parse HTML for {code}")
            return None
        
        # Basic info
        name_el = soup.select_one("h2.wrap_company a")
        name = name_el.get_text(strip=True) if name_el else ""
        
        # Current price and change
        no_today = soup.select_one("p.no_today")
        price = 0
        change = 0
        change_pct = 0.0
        if no_today:
            price_el = no_today.select_one("span.blind")
            if price_el:
                price = _to_int(price_el.get_text(strip=True))
            
            # Change
            change_el = soup.select_one("span.blind.sptxt")
            if change_el:
                change_text = change_el.find_next_sibling()
                if change_text:
                    change = _to_int(change_text.get_text(strip=True))
                    # Determine sign from parent class
                    parent = change_el.parent
                    if parent and "down" in parent.get("class", []):
                        change = -abs(change)
            
            # Change percentage
            pct_el = soup.select_one("span.blind.sptxt")
            if pct_el:
                pct_text = pct_el.find_next_sibling()
                if pct_text:
                    change_pct = _to_float(pct_text.get_text(strip=True))
                    if change < 0:
                        change_pct = -abs(change_pct)
        
        # Volume and trade value
        volume = 0
        trade_value = 0
        
        # Parse trade value from table row with <th class="title">거래대금(백만)</th> and <span id="_amount">
        # <th class="title">거래대금(백만)</th><td class="num"><span id="_amount">693</span></td>
        # 이미 백만 단위이므로 1,000,000 곱하기
        all_tables_for_amount = soup.select("table")
        for table in all_tables_for_amount:
            rows = table.select("tr")
            for row in rows:
                th = row.select_one("th.title, th")
                if th:
                    th_text = th.get_text(strip=True)
                    # "거래대금"이 포함되어 있고 "(백만)" 단위가 명시된 경우
                    if "거래대금" in th_text and ("백만" in th_text or "(백만)" in th_text):
                        td = row.select_one("td")
                        if td:
                            amount_span = td.select_one("span#_amount")
                            if amount_span:
                                amount_text = amount_span.get_text(strip=True)
                                amount_value = _to_int(amount_text)
                                if amount_value > 0:
                                    # 백만 단위이므로 1,000,000 곱하기
                                    trade_value = amount_value * 1_000_000
                                    break  # 찾았으면 중단
            if trade_value > 0:
                break  # 찾았으면 테이블 검색 중단
        
        # Parse volume from table structure
        # 거래량: <span class="sptxt sp_txt9">거래량</span> 다음 <em> 태그 안의 숫자들
        # 거래대금: <span class="sptxt sp_txt10">거래대금</span> 다음 <em> 태그 안의 숫자들, 그리고 <em> 다음 <span class="sptxt sp_txt11">백만</span>
        # 호가 정보 테이블은 제외해야 함 (summary="호가 정보에 관한표입니다.")
        summary_table = None
        all_tables = soup.select("table.type_2, table.type_tax, table.no_info")
        for table in all_tables:
            # 호가 정보 테이블 제외
            table_summary = table.get("summary", "")
            if "호가 정보" in table_summary or "호가정보" in table_summary:
                continue
            # "주요 시세" 또는 "시세" 관련 테이블 우선 선택
            if "주요 시세" in table_summary or "시세" in table_summary or "거래대금" in table_summary:
                summary_table = table
                break
        # 위에서 찾지 못했으면 호가 정보가 아닌 첫 번째 테이블 사용
        if not summary_table:
            for table in all_tables:
                table_summary = table.get("summary", "")
                if "호가 정보" not in table_summary and "호가정보" not in table_summary:
                    summary_table = table
                    break
        
        if summary_table and trade_value == 0:
            rows = summary_table.select("tr")
            for row in rows:
                # Find "거래량" or "거래대금" label
                label_span = row.select_one("span.sptxt")
                if label_span:
                    label_text = label_span.get_text(strip=True)
                    td = row.select_one("td")
                    if td:
                        # Find <em> tag after the label
                        em_tag = td.select_one("em")
                        if em_tag:
                            # Extract number from <em> tag - get all text (handles both blind and noX spans)
                            # 이미지 구조: <em> 안에 <span class="no4">4</span><span class="no2">2</span>... 형태
                            number_text = em_tag.get_text(strip=True)
                            number_value = _to_int(number_text)
                            
                            if "거래량" in label_text and volume == 0:
                                volume = number_value
                        
                        # Early exit if found
                        if volume > 0:
                            break
        
        # Method 3: Fallback to ID-based parsing for volume
        if volume == 0:
            quant_el = soup.select_one("span#_quant")
            if quant_el:
                volume = _to_int(quant_el.get_text(strip=True))
        
        # Market detection (KOSPI vs KOSDAQ)
        market = "KOSPI"
        if "코스닥" in html or "kosdaq" in html.lower():
            market = "KOSDAQ"
        
        # Previous day data for pivot (고가/저가/종가) - optimized for speed
        prev_high = None
        prev_low = None
        prev_close = None
        
        # Fast path: Try summary table first (most common location)
        if summary_table:
            rows = summary_table.select("tr")
            for row in rows:
                th = row.select_one("th")
                if th:
                    th_text = th.get_text(strip=True)
                    td = row.select_one("td")
                    if td:
                        td_text = td.get_text(strip=True)
                        if "전일" in th_text:
                            if "고가" in th_text:
                                prev_high = _to_float(td_text)
                            elif "저가" in th_text:
                                prev_low = _to_float(td_text)
                            elif "종가" in th_text:
                                prev_close = _to_float(td_text)
                        # Early exit if we found all three
                        if prev_high and prev_low and prev_close:
                            break
        
        # Quick fallback: estimate from current price if prev_close not found
        if not prev_close and price > 0:
            if change != 0:
                prev_close = price - change
            else:
                prev_close = price
        
        # Calculate pivot points immediately (don't wait for high/low)
        pivot_data = None
        if prev_close:
            # Use estimated high/low if not available (faster than searching more tables)
            if not prev_high:
                prev_high = prev_close * 1.05
            if not prev_low:
                prev_low = prev_close * 0.95
            pivot_data = calculate_pivot_points(prev_high, prev_low, prev_close)
        
        # Only search other tables if we still need high/low (optional, non-blocking)
        if not (prev_high and prev_low) and summary_table:
            # Quick scan of other tables (limited search for speed)
            all_tables = soup.select("table.type_1, table.tb_type1")[:2]  # Limit to 2 tables
            for table in all_tables:
                rows = table.select("tr")[:10]  # Limit to first 10 rows
                for row in rows:
                    cells = row.select("th, td")
                    for i, cell in enumerate(cells):
                        cell_text = cell.get_text(strip=True)
                        if "전일" in cell_text and i + 1 < len(cells):
                            if "고가" in cell_text and not prev_high:
                                prev_high = _to_float(cells[i + 1].get_text(strip=True))
                            elif "저가" in cell_text and not prev_low:
                                prev_low = _to_float(cells[i + 1].get_text(strip=True))
                        # Early exit if found
                        if prev_high and prev_low:
                            break
                    if prev_high and prev_low:
                        break
                if prev_high and prev_low:
                    break
            # Recalculate pivot if we found better high/low values
            if (prev_high and prev_low and prev_close and 
                (prev_high != prev_close * 1.05 or prev_low != prev_close * 0.95)):
                pivot_data = calculate_pivot_points(prev_high, prev_low, prev_close)
        
        # Fetch news from news section - improved parsing with more selectors
        news = []
        # Try multiple selectors for news (expanded list)
        news_selectors = [
            "div.news_area ul li a",
            "div#news ul li a",
            "table.news_table a",
            "div.section.news ul li a",
            "div.news_area a",
            "ul.news_list a",
            "div.news a",
            "dl.news_list dt a",
            "div.tab_con1 ul li a",
            "div.tab_con ul li a",
            "div.news_wrap ul li a",
            "div.news_list ul li a",
            "table.type_2 a[href*='news']",
            "div.cmp_news ul li a",
            "a[href*='/item/news']",  # Direct news links
            "a[href*='news.naver.com']",  # External news links
        ]
        for selector in news_selectors:
            news_items = soup.select(selector)
            if news_items:
                for item in news_items[:15]:  # Check more items
                    title = item.get_text(strip=True)
                    href = item.get("href", "")
                    # More lenient title filter - accept any meaningful title
                    if title and len(title) > 2 and not any(skip in title for skip in ["더보기", "전체보기", "▼", "▲", "펼치기"]):
                        # Clean title: ensure proper UTF-8 encoding
                        try:
                            # BeautifulSoup should already handle encoding, but ensure it's clean
                            title_clean = title.strip()
                            # Remove any control characters that might cause issues
                            title_clean = ''.join(char for char in title_clean if ord(char) >= 32 or char in '\n\r\t')
                        except Exception:
                            title_clean = title.strip()
                        
                        # Extract date
                        date = ""
                        parent = item.parent
                        if parent:
                            date_el = parent.select_one("span.date, span.time, em.date, span.info, em.info, span.txt")
                            if date_el:
                                date = date_el.get_text(strip=True)
                            # Also check siblings
                            for sibling in parent.find_next_siblings():
                                if sibling.name in ["span", "em"] and ("date" in sibling.get("class", []) or "time" in sibling.get("class", [])):
                                    date = sibling.get_text(strip=True)
                                    break
                            # Check parent's parent for date
                            if not date and parent.parent:
                                date_el = parent.parent.select_one("span.date, span.time, em.date, em.info, span.txt")
                                if date_el:
                                    date = date_el.get_text(strip=True)
                        
                        # Build full URL
                        if href.startswith("/"):
                            full_url = f"https://finance.naver.com{href}"
                        elif href.startswith("http"):
                            full_url = href
                        elif href:
                            full_url = f"https://finance.naver.com/{href}"
                        else:
                            continue  # Skip if no valid href
                        
                        # Avoid duplicates
                        if not any(n.get("url") == full_url for n in news):
                            # Ensure proper UTF-8 encoding for title
                            try:
                                # Clean title: remove any invalid characters
                                title_clean = title.encode('utf-8', errors='ignore').decode('utf-8')
                                news.append({
                                    "title": title_clean,
                                    "date": date,
                                    "url": full_url,
                                })
                            except Exception:
                                # Fallback: use original title
                                news.append({
                                    "title": title,
                                    "date": date,
                                    "url": full_url,
                                })
                            if len(news) >= 5:  # Stop at 5 news items
                                break
                if len(news) >= 5:
                    break  # Found enough news, stop trying other selectors
        
        if news:
            print(f"[{code}] Found {len(news)} news items")
        else:
            print(f"[{code}] No news found in main page")
        
        # If no news found, try fetching from news page (parallel fetch for speed)
        if not news:
            try:
                # Use shorter timeout for news page
                news_res = await client.get(
                    f"https://finance.naver.com/item/news.naver?code={code}",
                    follow_redirects=True,
                    timeout=10.0
                )
                news_res.encoding = "euc-kr"
                news_html = news_res.text
                news_soup = BeautifulSoup(news_html, "html.parser")
                # More comprehensive selectors for news page
                news_items = news_soup.select(
                    "dl dt a, table.news_table a, ul.news_list a, "
                    "div.news_area ul li a, div#news ul li a, "
                    "div.tab_con1 ul li a, div.news_list ul li a"
                )
                for item in news_items[:10]:
                    title = item.get_text(strip=True)
                    href = item.get("href", "")
                    if title and len(title) > 3 and not title.startswith("더보기"):
                        # Clean title: ensure proper UTF-8 encoding
                        try:
                            title_clean = title.strip()
                            # Remove any control characters that might cause issues
                            title_clean = ''.join(char for char in title_clean if ord(char) >= 32 or char in '\n\r\t')
                        except Exception:
                            title_clean = title.strip()
                        
                        if href.startswith("/"):
                            full_url = f"https://finance.naver.com{href}"
                        elif href.startswith("http"):
                            full_url = href
                        elif href:
                            full_url = f"https://finance.naver.com/{href}"
                        else:
                            continue
                        
                        # Avoid duplicates
                        if not any(n.get("url") == full_url for n in news):
                            news.append({
                                "title": title_clean,
                                "date": "",
                                "url": full_url,
                            })
                            if len(news) >= 5:
                                break
            except Exception as e:
                print(f"Warning: Failed to fetch news page for {code}: {e}")
                # Continue without news - don't block the response
        
        # Financial summary (재무 요약) - parse from QUARTERLY financial table (not annual)
        # Try main page first (already loaded) for speed
        financials = []
        # First try main page (already loaded) - prioritize QUARTERLY tables over annual
        # Look for quarterly table first (최근 분기 실적)
        fin_tables = soup.select("table.type_2, table.tb_type1, table.tb_type1_ifrs, table.sise")
        
        # Separate quarterly and annual tables
        quarterly_tables = []
        annual_tables = []
        
        for table in fin_tables:
            # Check caption or nearby text to identify table type
            caption = table.select_one("caption")
            caption_text = caption.get_text(strip=True) if caption else ""
            
            # Check parent div or h4 for table title
            parent = table.find_parent(["div", "section"])
            parent_text = parent.get_text(strip=True) if parent else ""
            
            # Check if this is a quarterly table (분기)
            if "분기" in caption_text or "분기" in parent_text:
                quarterly_tables.append(table)
            # Check if this is an annual table (연간) - we want to skip this
            elif "연간" in caption_text or "연간" in parent_text:
                annual_tables.append(table)
            else:
                # If unclear, check column headers for quarterly patterns
                # 분기 실적: 03, 06, 09, 12월이 섞여 있어야 함
                # 연간 실적: 모든 컬럼이 12월이면 연간
                thead = table.select_one("thead")
                if thead:
                    headers = thead.select("th")
                    header_texts = [h.get_text(strip=True) for h in headers]
                    # Extract all date periods from headers
                    date_periods = []
                    for h_text in header_texts:
                        period_match = re.match(r'(\d{4})\.(\d{1,2})', h_text)
                        if period_match:
                            year = int(period_match.group(1))
                            month = int(period_match.group(2))
                            date_periods.append((year, month))
                    
                    if len(date_periods) > 0:
                        # Check if all months are December (연간 실적 패턴)
                        all_december = all(month == 12 for _, month in date_periods)
                        if all_december:
                            # 연간 실적 테이블로 분류
                            annual_tables.append(table)
                        else:
                            # 03, 06, 09, 12월이 섞여 있으면 분기 실적
                            months = [month for _, month in date_periods]
                            has_quarterly_months = any(m in [3, 6, 9, 12] for m in months)
                            if has_quarterly_months:
                                quarterly_tables.append(table)
                            else:
                                # 불명확한 경우 연간으로 분류 (안전하게)
                                annual_tables.append(table)
                else:
                    # If no thead, check first row
                    first_row = table.select_one("tr")
                    if first_row:
                        first_row_ths = first_row.select("th")
                        date_periods = []
                        for th in first_row_ths:
                            h_text = th.get_text(strip=True)
                            period_match = re.match(r'(\d{4})\.(\d{1,2})', h_text)
                            if period_match:
                                year = int(period_match.group(1))
                                month = int(period_match.group(2))
                                date_periods.append((year, month))
                        
                        if len(date_periods) > 0:
                            all_december = all(month == 12 for _, month in date_periods)
                            if all_december:
                                annual_tables.append(table)
                            else:
                                months = [month for _, month in date_periods]
                                has_quarterly_months = any(m in [3, 6, 9, 12] for m in months)
                                if has_quarterly_months:
                                    quarterly_tables.append(table)
                                else:
                                    annual_tables.append(table)
        
        # Process quarterly tables first (우선순위)
        for table in quarterly_tables:
            # 컬럼 헤더 찾기 (scope="col" 또는 thead 내부)
            thead = table.select_one("thead")
            col_headers = []
            if thead:
                col_headers = thead.select("th[scope='col'], th")
            else:
                # thead가 없으면 첫 번째 행의 th를 컬럼 헤더로 간주
                first_row = table.select_one("tr")
                if first_row:
                    col_headers = first_row.select("th")
            
            col_header_texts = [h.get_text(strip=True) for h in col_headers]
            
            # 행 헤더에서 매출액/영업이익 찾기 (scope="row")
            # 우선순위: "(억원)" 단위가 있는 절대값 데이터만 (비율 데이터 제외)
            rows = table.select("tr")
            sales_row_idx = None
            profit_row_idx = None
            
            for i, row in enumerate(rows):
                row_headers = row.select("th[scope='row'], th.h_th2")
                for rh in row_headers:
                    rh_text = rh.get_text(strip=True)
                    # 매출액: "(억원)" 단위가 있는 것만 (비율 제외)
                    if "매출액" in rh_text and "(억원)" in rh_text:
                        if sales_row_idx is None:  # First match takes priority
                            sales_row_idx = i
                    elif "매출액" in rh_text and "매출원가" not in rh_text and "%" not in rh_text and "률" not in rh_text:
                        # Fallback: 매출액이지만 비율이 아닌 경우
                        if sales_row_idx is None:
                            sales_row_idx = i
                    # 영업이익: "(억원)" 단위가 있는 것만 (비율 제외)
                    elif ("영업이익" in rh_text or "영업손익" in rh_text) and "(억원)" in rh_text:
                        if profit_row_idx is None:  # First match takes priority
                            profit_row_idx = i
                    elif ("영업이익" in rh_text or "영업손익" in rh_text) and "%" not in rh_text and "률" not in rh_text:
                        # Fallback: 영업이익이지만 비율이 아닌 경우
                        if profit_row_idx is None:
                            profit_row_idx = i
            
            # 매출액/영업이익 행이 있으면 파싱
            if sales_row_idx is not None or profit_row_idx is not None:
                # 컬럼 헤더에서 기간 정보 추출 (YYYY.MM 형식)
                # thead의 th[scope='col']에서 날짜 헤더 찾기
                periods = []
                period_col_indices = []  # 각 period의 실제 컬럼 인덱스
                
                # thead에서 직접 컬럼 헤더와 인덱스 매핑
                if thead:
                    thead_rows = thead.select("tr")
                    for thead_row in thead_rows:
                        thead_ths = thead_row.select("th[scope='col'], th")
                        for col_idx, th in enumerate(thead_ths):
                            h_text = th.get_text(strip=True)
                            # (E)가 포함된 컬럼은 완전히 제외, 실제 데이터만 사용
                            if re.match(r'\d{4}\.\d{1,2}', h_text) and "(E)" not in h_text and "(e)" not in h_text:
                                # YYYY.MM 형식만 추출
                                period_match = re.match(r'(\d{4}\.\d{1,2})', h_text)
                                if period_match:
                                    period = period_match.group(1)
                                    if period not in periods:
                                        periods.append(period)
                                        period_col_indices.append(col_idx)
                else:
                    # thead가 없으면 첫 번째 행의 th에서 찾기
                    first_row = table.select_one("tr")
                    if first_row:
                        first_row_ths = first_row.select("th")
                        for col_idx, th in enumerate(first_row_ths):
                            h_text = th.get_text(strip=True)
                            # (E)가 포함된 컬럼은 완전히 제외, 실제 데이터만 사용
                            if re.match(r'\d{4}\.\d{1,2}', h_text) and "(E)" not in h_text and "(e)" not in h_text:
                                period_match = re.match(r'(\d{4}\.\d{1,2})', h_text)
                                if period_match:
                                    period = period_match.group(1)
                                    if period not in periods:
                                        periods.append(period)
                                        period_col_indices.append(col_idx)
                
                # 최근 4개 기간만 (최신순) - 날짜를 파싱해서 정렬
                # 날짜 형식: YYYY.MM 또는 YYYY.MM.DD
                def parse_period(period_str):
                    """Parse period string to tuple for sorting (year, month)"""
                    match = re.match(r'(\d{4})\.(\d{1,2})', period_str)
                    if match:
                        return (int(match.group(1)), int(match.group(2)))
                    return (0, 0)
                
                # Sort periods by date (newest first), then take first 4
                period_data = list(zip(periods, period_col_indices))
                period_data.sort(key=lambda x: parse_period(x[0]), reverse=True)  # 최신순
                period_data = period_data[:4]  # 최근 4개만
                
                # Unzip back to lists
                periods = [p[0] for p in period_data]
                period_col_indices = [p[1] for p in period_data]
                
                # 매출액/영업이익 행의 데이터 가져오기
                for period_idx, period in enumerate(periods):
                    sales = 0.0
                    profit = 0.0
                    
                    # 컬럼 인덱스 사용 (thead에서 찾은 정확한 인덱스)
                    if period_idx < len(period_col_indices):
                        col_idx = period_col_indices[period_idx]
                    else:
                        # Fallback: period_idx + 1 (첫 번째 컬럼이 행 헤더일 수 있음)
                        col_idx = period_idx + 1
                    
                    # 매출액 행에서 값 가져오기
                    if sales_row_idx is not None and sales_row_idx < len(rows):
                        sales_row = rows[sales_row_idx]
                        sales_tds = sales_row.select("td")
                        if col_idx < len(sales_tds):
                            sales = _to_float(sales_tds[col_idx].get_text(strip=True))
                    
                    # 영업이익 행에서 값 가져오기
                    if profit_row_idx is not None and profit_row_idx < len(rows):
                        profit_row = rows[profit_row_idx]
                        profit_tds = profit_row.select("td")
                        if col_idx < len(profit_tds):
                            profit = _to_float(profit_tds[col_idx].get_text(strip=True))
                    
                    # 실제 데이터가 있는 경우만 추가 (sales와 profit이 모두 0이면 제외)
                    # 단, 음수 영업이익은 유효한 데이터이므로 포함
                    if sales != 0 or profit != 0:
                        financials.append({
                            "period": period,
                            "sales": sales,
                            "operating_profit": profit,
                        })
                
                if len(financials) > 0:
                    break
            
            # Skip fallback for this table - we already processed quarterly tables above
        
        # If no quarterly data found, try other tables (but skip annual tables)
        # 연간 실적 테이블은 완전히 제외
        if len(financials) == 0:
            for table in fin_tables:
                # Skip if this is an annual table (이미 분류된 연간 테이블 제외)
                if table in annual_tables:
                    continue
                
                # Skip if this is an annual table (텍스트 기반 체크)
                caption = table.select_one("caption")
                caption_text = caption.get_text(strip=True) if caption else ""
                parent = table.find_parent(["div", "section"])
                parent_text = parent.get_text(strip=True) if parent else ""
                if "연간" in caption_text or "연간" in parent_text:
                    continue  # Skip annual tables
                
                # Try the same parsing logic
                thead = table.select_one("thead")
                col_headers = []
                if thead:
                    col_headers = thead.select("th[scope='col'], th")
                else:
                    first_row = table.select_one("tr")
                    if first_row:
                        col_headers = first_row.select("th")
                
                col_header_texts = [h.get_text(strip=True) for h in col_headers]
                rows = table.select("tr")
                sales_row_idx = None
                profit_row_idx = None
                
                for i, row in enumerate(rows):
                    row_headers = row.select("th[scope='row'], th.h_th2")
                    for rh in row_headers:
                        rh_text = rh.get_text(strip=True)
                        if "매출액" in rh_text and "(억원)" in rh_text:
                            if sales_row_idx is None:
                                sales_row_idx = i
                        elif "매출액" in rh_text and "매출원가" not in rh_text and "%" not in rh_text and "률" not in rh_text:
                            if sales_row_idx is None:
                                sales_row_idx = i
                        elif ("영업이익" in rh_text or "영업손익" in rh_text) and "(억원)" in rh_text:
                            if profit_row_idx is None:
                                profit_row_idx = i
                        elif ("영업이익" in rh_text or "영업손익" in rh_text) and "%" not in rh_text and "률" not in rh_text:
                            if profit_row_idx is None:
                                profit_row_idx = i
                
                if sales_row_idx is not None or profit_row_idx is not None:
                    # Same parsing logic as above
                    periods = []
                    period_col_indices = []
                    
                    if thead:
                        thead_rows = thead.select("tr")
                        for thead_row in thead_rows:
                            thead_ths = thead_row.select("th[scope='col'], th")
                            for col_idx, th in enumerate(thead_ths):
                                h_text = th.get_text(strip=True)
                                # (E)가 포함된 컬럼은 완전히 제외, 실제 데이터만 사용
                                if re.match(r'\d{4}\.\d{1,2}', h_text) and "(E)" not in h_text and "(e)" not in h_text:
                                    period_match = re.match(r'(\d{4}\.\d{1,2})', h_text)
                                    if period_match:
                                        period = period_match.group(1)
                                        if period not in periods:
                                            periods.append(period)
                                            period_col_indices.append(col_idx)
                    
                    # 최근 4개 기간만 (최신순) - 날짜를 파싱해서 정렬
                    def parse_period(period_str):
                        """Parse period string to tuple for sorting (year, month)"""
                        match = re.match(r'(\d{4})\.(\d{1,2})', period_str)
                        if match:
                            return (int(match.group(1)), int(match.group(2)))
                        return (0, 0)
                    
                    # Sort periods by date (newest first), then take first 4
                    period_data = list(zip(periods, period_col_indices))
                    period_data.sort(key=lambda x: parse_period(x[0]), reverse=True)  # 최신순
                    period_data = period_data[:4]  # 최근 4개만
                    
                    # Unzip back to lists
                    periods = [p[0] for p in period_data]
                    period_col_indices = [p[1] for p in period_data]
                    
                    for period_idx, period in enumerate(periods):
                        sales = 0.0
                        profit = 0.0
                        
                        if period_idx < len(period_col_indices):
                            col_idx = period_col_indices[period_idx]
                        else:
                            col_idx = period_idx + 1
                        
                        if sales_row_idx is not None and sales_row_idx < len(rows):
                            sales_row = rows[sales_row_idx]
                            sales_tds = sales_row.select("td")
                            if col_idx < len(sales_tds):
                                sales = _to_float(sales_tds[col_idx].get_text(strip=True))
                        
                        if profit_row_idx is not None and profit_row_idx < len(rows):
                            profit_row = rows[profit_row_idx]
                            profit_tds = profit_row.select("td")
                            if col_idx < len(profit_tds):
                                profit = _to_float(profit_tds[col_idx].get_text(strip=True))
                        
                        # 실제 데이터가 있는 경우만 추가
                        if sales != 0 or profit != 0:
                            financials.append({
                                "period": period,
                                "sales": sales,
                                "operating_profit": profit,
                            })
                    
                    if len(financials) > 0:
                        break
        
        # Convert financials from list to date-keyed object structure
        # Structure: {"2024.12": {"sales": 195, "operating_profit": -10}, ...}
        financials_dict = {}
        if financials:
            def parse_period_for_sort(period_str):
                """Parse period string to tuple for sorting (year, month)"""
                match = re.match(r'(\d{4})\.(\d{1,2})', period_str)
                if match:
                    return (int(match.group(1)), int(match.group(2)))
                return (0, 0)
            
            # Sort by period (newest first) to ensure consistent ordering
            financials.sort(key=lambda x: parse_period_for_sort(x.get("period", "")), reverse=True)
            
            # Convert to date-keyed dictionary
            for f in financials:
                period = f.get("period", "")
                if period:
                    financials_dict[period] = {
                        "sales": f.get("sales", 0.0),
                        "operating_profit": f.get("operating_profit", 0.0),
                    }
            
            print(f"[{code}] Found {len(financials_dict)} financial records (quarterly)")
        else:
            print(f"[{code}] No quarterly financial data found in main page")
        
        # Use dictionary structure instead of list (empty dict becomes None)
        financials = financials_dict if financials_dict else None
        
        # Only try other pages if not found in main page (to speed up)
        # Skip - we prioritize quarterly tables from main page
        
        # Investor trends (투자자별 매매동향) - parse from investor table
        # Try main page first (already loaded) for speed
        investor_trends = []
        inv_tables = soup.select("table.type_2, table.tb_type1, table.type_1, table.sise")
        
        # 우선순위: summary 속성에 "외국인" 또는 "기관" 또는 "순매매"가 포함된 테이블
        priority_tables = []
        other_tables = []
        
        for table in inv_tables:
            table_summary = table.get("summary", "")
            caption = table.select_one("caption")
            caption_text = caption.get_text(strip=True) if caption else ""
            
            # 우선순위 테이블: summary나 caption에 투자자 관련 키워드가 있는 경우
            if any(keyword in table_summary or keyword in caption_text 
                   for keyword in ["외국인", "기관", "순매매", "매매동향", "투자자"]):
                priority_tables.append(table)
            else:
                other_tables.append(table)
        
        # 우선순위 테이블부터 처리
        tables_to_check = priority_tables + other_tables
        
        for table in tables_to_check:
            headers = table.select("th")
            header_texts = [h.get_text(strip=True) for h in headers]
            has_institution = any("기관" in h or "기관투자자" in h for h in header_texts)
            has_foreigner = any("외국인" in h or "외국인투자자" in h for h in header_texts)
            
            # 호가 정보 테이블 제외
            table_summary = table.get("summary", "")
            if "호가 정보" in table_summary or "호가정보" in table_summary:
                continue
            
            if has_institution and has_foreigner:
                # 컬럼 헤더만 찾기 (scope="col" 또는 thead 내부)
                inv_thead = table.select_one("thead")
                col_headers = []
                if inv_thead:
                    # thead의 모든 tr에서 th 찾기
                    thead_rows = inv_thead.select("tr")
                    for thead_row in thead_rows:
                        col_headers.extend(thead_row.select("th[scope='col'], th"))
                else:
                    # thead가 없으면 첫 번째 행의 th를 컬럼 헤더로 간주
                    first_row = table.select_one("tr")
                    if first_row:
                        col_headers = first_row.select("th")
                
                col_header_texts = [h.get_text(strip=True) for h in col_headers]
                
                # 헤더에서 정확한 컬럼 인덱스 찾기
                # 테이블 구조: 날짜, 종가, 전일비, 등락률, 거래량, 기관(순매매량), 외국인(순매매량), 외국인(보유주수), 외국인(보유율)
                date_idx = None
                institution_idx = None
                foreigner_idx = None
                foreigner_shares_idx = None
                foreigner_ratio_idx = None
                
                # 2행 헤더 구조 처리: 첫 번째 행과 두 번째 행 모두 확인
                for i, header in enumerate(col_header_texts):
                    header_lower = header.lower()
                    if "날짜" in header or "일자" in header or "date" in header_lower:
                        date_idx = i
                    elif "기관" in header and "순매매" in header:
                        institution_idx = i
                    elif "외국인" in header and "순매매" in header:
                        foreigner_idx = i
                    elif "외국인" in header and ("보유주수" in header or "보유" in header) and "율" not in header:
                        foreigner_shares_idx = i
                    elif "외국인" in header and ("보유율" in header or "율" in header):
                        foreigner_ratio_idx = i
                
                # Fallback: 헤더 텍스트가 정확히 매칭되지 않은 경우 위치 기반으로 추정
                # 일반적인 순서: 날짜(0), 종가(1), 전일비(2), 등락률(3), 거래량(4), 기관(5), 외국인(6), 외국인보유주수(7), 외국인보유율(8)
                if institution_idx is None and len(col_header_texts) > 5:
                    # "기관"이 포함된 헤더 찾기
                    for i, header in enumerate(col_header_texts):
                        if "기관" in header and institution_idx is None:
                            institution_idx = i
                            break
                
                if foreigner_idx is None and len(col_header_texts) > 6:
                    # "외국인"이 포함되고 "순매매"가 있는 헤더 찾기
                    for i, header in enumerate(col_header_texts):
                        if "외국인" in header and "순매매" in header and foreigner_idx is None:
                            foreigner_idx = i
                            break
                
                if foreigner_shares_idx is None and len(col_header_texts) > 7:
                    # "외국인"이 포함되고 "보유주수"가 있는 헤더 찾기
                    for i, header in enumerate(col_header_texts):
                        if "외국인" in header and ("보유주수" in header or "보유" in header) and "율" not in header and foreigner_shares_idx is None:
                            foreigner_shares_idx = i
                            break
                
                if foreigner_ratio_idx is None and len(col_header_texts) > 8:
                    # "외국인"이 포함되고 "보유율"이 있는 헤더 찾기
                    for i, header in enumerate(col_header_texts):
                        if "외국인" in header and ("보유율" in header or "율" in header) and foreigner_ratio_idx is None:
                            foreigner_ratio_idx = i
                            break
                
                rows = table.select("tr")
                for row in rows[1:]:  # Skip header
                    tds = row.select("td")
                    if len(tds) < 2:
                        continue
                    
                    # 헤더 매칭으로 정확한 컬럼 사용
                    if date_idx is not None and date_idx < len(tds):
                        date = tds[date_idx].get_text(strip=True)
                    else:
                        date = tds[0].get_text(strip=True)  # Fallback
                    
                    # Skip if date is empty or looks like a header
                    if not date or date in ["날짜", "일자", "구분", "Date"]:
                        continue
                    
                    # 날짜 형식 검증 (YYYY.MM.DD 또는 YYYY-MM-DD 형식만 허용)
                    date_clean = date.strip() if date else ""
                    is_valid_date = False
                    if date_clean:
                        # YYYY.MM.DD 또는 YYYY-MM-DD 형식 확인
                        if re.match(r'\d{4}[\.-]\d{1,2}[\.-]\d{1,2}', date_clean):
                            is_valid_date = True
                        # 숫자만 있는 경우 스킵 (종가 등)
                        elif date_clean.replace(",", "").replace(".", "").replace("-", "").isdigit():
                            is_valid_date = False
                    
                    if not is_valid_date:
                        continue
                    
                    # 헤더 매칭으로 기관/외국인 값 가져오기
                    institution = 0
                    foreigner = 0
                    foreigner_shares = 0
                    foreigner_ratio = 0.0
                    
                    if institution_idx is not None and institution_idx < len(tds):
                        institution_text = tds[institution_idx].get_text(strip=True)
                        institution = _to_int(institution_text)
                    elif len(tds) > 5:
                        # Fallback: 6번째 컬럼(인덱스 5)이 기관일 가능성
                        institution = _to_int(tds[5].get_text(strip=True))
                    
                    if foreigner_idx is not None and foreigner_idx < len(tds):
                        foreigner_text = tds[foreigner_idx].get_text(strip=True)
                        foreigner = _to_int(foreigner_text)
                    elif len(tds) > 6:
                        # Fallback: 7번째 컬럼(인덱스 6)이 외국인 순매매량일 가능성
                        foreigner = _to_int(tds[6].get_text(strip=True))
                    
                    if foreigner_shares_idx is not None and foreigner_shares_idx < len(tds):
                        foreigner_shares_text = tds[foreigner_shares_idx].get_text(strip=True)
                        foreigner_shares = _to_int(foreigner_shares_text)
                    elif len(tds) > 7:
                        # Fallback: 8번째 컬럼(인덱스 7)이 외국인 보유주수일 가능성
                        foreigner_shares = _to_int(tds[7].get_text(strip=True))
                    
                    if foreigner_ratio_idx is not None and foreigner_ratio_idx < len(tds):
                        foreigner_ratio_text = tds[foreigner_ratio_idx].get_text(strip=True)
                        foreigner_ratio = _to_float(foreigner_ratio_text)
                    elif len(tds) > 8:
                        # Fallback: 9번째 컬럼(인덱스 8)이 외국인 보유율일 가능성
                        foreigner_ratio = _to_float(tds[8].get_text(strip=True))
                    
                    investor_trends.append({
                        "date": date_clean,
                        "institution": institution,
                        "foreigner": foreigner,
                        "foreigner_shares": foreigner_shares,
                        "foreigner_ratio": foreigner_ratio,
                    })
                    if len(investor_trends) >= 5:  # Recent 5 days
                        break
                if len(investor_trends) > 0:
                    break
        
        if investor_trends:
            print(f"[{code}] Found {len(investor_trends)} investor trend records")
        else:
            print(f"[{code}] No investor trend data found in main page")
        
        # Only try other pages if not found in main page (to speed up)
        if not investor_trends:
            investor_pages = [
                f"https://finance.naver.com/item/frgn.naver?code={code}",
            ]
            for inv_url in investor_pages:
                try:
                    inv_html = await _get(client, inv_url)
                    inv_soup = BeautifulSoup(inv_html, "html.parser")
                    inv_tables = inv_soup.select("table.type_2, table.tb_type1, table.sise, table.type_1")
                    for table in inv_tables:
                        headers = table.select("th")
                        header_texts = [h.get_text(strip=True) for h in headers]
                        has_institution = any("기관" in h for h in header_texts)
                        has_foreigner = any("외국인" in h for h in header_texts)
                        
                        if has_institution and has_foreigner:
                            # 컬럼 헤더만 찾기 (scope="col" 또는 thead 내부)
                            inv_thead = table.select_one("thead")
                            col_headers = []
                            if inv_thead:
                                # thead의 모든 tr에서 th 찾기
                                thead_rows = inv_thead.select("tr")
                                for thead_row in thead_rows:
                                    col_headers.extend(thead_row.select("th[scope='col'], th"))
                            else:
                                first_row = table.select_one("tr")
                                if first_row:
                                    col_headers = first_row.select("th")
                            
                            col_header_texts = [h.get_text(strip=True) for h in col_headers]
                            
                            # 헤더에서 정확한 컬럼 인덱스 찾기
                            date_idx = None
                            institution_idx = None
                            foreigner_idx = None
                            foreigner_shares_idx = None
                            foreigner_ratio_idx = None
                            
                            for i, header in enumerate(col_header_texts):
                                header_lower = header.lower()
                                if "날짜" in header or "일자" in header or "date" in header_lower:
                                    date_idx = i
                                elif "기관" in header and "순매매" in header:
                                    institution_idx = i
                                elif "외국인" in header and "순매매" in header:
                                    foreigner_idx = i
                                elif "외국인" in header and ("보유주수" in header or "보유" in header) and "율" not in header:
                                    foreigner_shares_idx = i
                                elif "외국인" in header and ("보유율" in header or "율" in header):
                                    foreigner_ratio_idx = i
                            
                            # Fallback: 위치 기반 추정
                            if institution_idx is None and len(col_header_texts) > 5:
                                for i, header in enumerate(col_header_texts):
                                    if "기관" in header and institution_idx is None:
                                        institution_idx = i
                                        break
                            
                            if foreigner_idx is None and len(col_header_texts) > 6:
                                for i, header in enumerate(col_header_texts):
                                    if "외국인" in header and "순매매" in header and foreigner_idx is None:
                                        foreigner_idx = i
                                        break
                            
                            if foreigner_shares_idx is None and len(col_header_texts) > 7:
                                for i, header in enumerate(col_header_texts):
                                    if "외국인" in header and ("보유주수" in header or "보유" in header) and "율" not in header and foreigner_shares_idx is None:
                                        foreigner_shares_idx = i
                                        break
                            
                            if foreigner_ratio_idx is None and len(col_header_texts) > 8:
                                for i, header in enumerate(col_header_texts):
                                    if "외국인" in header and ("보유율" in header or "율" in header) and foreigner_ratio_idx is None:
                                        foreigner_ratio_idx = i
                                        break
                            
                            rows = table.select("tr")
                            for row in rows[1:]:  # Skip header
                                tds = row.select("td")
                                if len(tds) < 2:
                                    continue
                                
                                # 헤더 매칭으로 정확한 컬럼 사용
                                if date_idx is not None and date_idx < len(tds):
                                    date = tds[date_idx].get_text(strip=True)
                                else:
                                    date = tds[0].get_text(strip=True)  # Fallback
                                
                                # Skip if date is empty or looks like a header
                                if not date or date in ["날짜", "일자", "구분", "Date"]:
                                    continue
                                
                                # 날짜 형식 검증 (YYYY.MM.DD 또는 YYYY-MM-DD 형식만 허용)
                                date_clean = date.strip() if date else ""
                                is_valid_date = False
                                if date_clean:
                                    # YYYY.MM.DD 또는 YYYY-MM-DD 형식 확인
                                    if re.match(r'\d{4}[\.-]\d{1,2}[\.-]\d{1,2}', date_clean):
                                        is_valid_date = True
                                    # 숫자만 있는 경우 스킵 (종가 등)
                                    elif date_clean.replace(",", "").replace(".", "").replace("-", "").isdigit():
                                        is_valid_date = False
                                
                                if not is_valid_date:
                                    continue
                                
                                institution = 0
                                foreigner = 0
                                foreigner_shares = 0
                                foreigner_ratio = 0.0
                                
                                if institution_idx is not None and institution_idx < len(tds):
                                    institution_text = tds[institution_idx].get_text(strip=True)
                                    institution = _to_int(institution_text)
                                elif len(tds) > 5:
                                    institution = _to_int(tds[5].get_text(strip=True))
                                
                                if foreigner_idx is not None and foreigner_idx < len(tds):
                                    foreigner_text = tds[foreigner_idx].get_text(strip=True)
                                    foreigner = _to_int(foreigner_text)
                                elif len(tds) > 6:
                                    foreigner = _to_int(tds[6].get_text(strip=True))
                                
                                if foreigner_shares_idx is not None and foreigner_shares_idx < len(tds):
                                    foreigner_shares_text = tds[foreigner_shares_idx].get_text(strip=True)
                                    foreigner_shares = _to_int(foreigner_shares_text)
                                elif len(tds) > 7:
                                    foreigner_shares = _to_int(tds[7].get_text(strip=True))
                                
                                if foreigner_ratio_idx is not None and foreigner_ratio_idx < len(tds):
                                    foreigner_ratio_text = tds[foreigner_ratio_idx].get_text(strip=True)
                                    foreigner_ratio = _to_float(foreigner_ratio_text)
                                elif len(tds) > 8:
                                    foreigner_ratio = _to_float(tds[8].get_text(strip=True))
                                
                                investor_trends.append({
                                    "date": date_clean,
                                    "institution": institution,
                                    "foreigner": foreigner,
                                    "foreigner_shares": foreigner_shares,
                                    "foreigner_ratio": foreigner_ratio,
                                })
                                if len(investor_trends) >= 5:  # Recent 5 days
                                    break
                            if len(investor_trends) > 0:
                                break
                            if len(investor_trends) > 0:
                                break
                    if len(investor_trends) > 0:
                        break
                except Exception as e:
                    print(f"Warning: Failed to fetch investor page {inv_url} for {code}: {e}")
                    continue
        
        return StockDetail(
            code=code,
            name=name,
            price=price,
            change=change,
            change_pct=change_pct,
            volume=volume,
            trade_value=trade_value,
            market=market,
            pivot=pivot_data["pivot"] if pivot_data else None,
            r1=pivot_data["r1"] if pivot_data else None,
            r2=pivot_data["r2"] if pivot_data else None,
            s1=pivot_data["s1"] if pivot_data else None,
            s2=pivot_data["s2"] if pivot_data else None,
            prev_high=prev_high,
            prev_low=prev_low,
            prev_close=prev_close,
            news=news if news else [],
            financials=financials,
            investor_trends=investor_trends,
        )
    except Exception as e:
        print(f"Error fetching stock detail for {code}: {e}")
        return None


