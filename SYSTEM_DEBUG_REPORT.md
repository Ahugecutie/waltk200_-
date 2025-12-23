# 🔍 전체 시스템 디버깅 리포트

## 📋 문제 요약

**현상**: Render 배포 서버가 정상 기동하지만 브라우저/API 접근 시 404 발생
**영향**: Android WebView 앱에서 데이터 수신 실패
**서버 상태**: Uvicorn 정상 실행 중 (로그 확인됨)

---

## 1️⃣ 서버 코드 구조 점검

### ✅ 정의된 FastAPI 라우트 목록

| 라우트 | 메서드 | 위치 | 상태 |
|--------|--------|------|------|
| `/health` | GET | Line 55-61 | ✅ 정상 정의 |
| `/snapshot` | GET | Line 67-80 | ✅ 정상 정의 |
| `/refresh` | POST | Line 83-90 | ✅ 정상 정의 |
| `/stock/{code}` | GET | Line 96-153 | ✅ 정상 정의 |
| `/ws` | WebSocket | Line 187-204 | ✅ 정상 정의 |
| `/` (루트) | - | **없음** | ❌ **미정의** |

### 🔍 라우트 접근 가능성 검증

**논리적 검증 결과**:
- 모든 API 라우트는 `@app.get()` 또는 `@app.post()` 데코레이터로 정상 등록됨
- FastAPI는 등록된 라우트를 자동으로 라우팅 테이블에 추가
- **단, 루트 경로(`/`)에 대한 라우트가 없음**

### ❌ GET / (루트 경로) 404 원인

**원인**: `server/main.py`에 루트 경로(`/`)에 대한 라우트가 정의되어 있지 않음

**코드 근거**:
```python
# Line 32: FastAPI 앱 생성
app = FastAPI(title="LeadingStock API", version="0.1.0")

# Line 36-40: StaticFiles는 /app에만 마운트
app.mount("/app", StaticFiles(...), name="mobile")

# Line 55 이후: API 라우트만 정의됨
@app.get("/health")  # ✅
@app.get("/snapshot")  # ✅
# ... / (루트) 라우트 없음 ❌
```

**결론**: 
- 브라우저에서 `https://your-app.onrender.com/` 접근 시 → 404 발생 (정상)
- 브라우저에서 `https://your-app.onrender.com/health` 접근 시 → 200 OK (정상)
- 브라우저에서 `https://your-app.onrender.com/app/` 접근 시 → 200 OK (StaticFiles)

---

## 2️⃣ StaticFiles 마운트 영향 분석

### ✅ 현재 마운트 구조

```python
# Line 36-40
app.mount(
    "/app",
    StaticFiles(directory=str(MOBILE_DIR), html=True),
    name="mobile"
)
```

### 🔍 StaticFiles가 API 라우트를 덮어쓰는가?

**결론**: **아니오. 덮어쓰지 않음**

**이유**:
1. FastAPI 라우팅 우선순위: 명시적 라우트(`@app.get()`)가 마운트된 StaticFiles보다 우선
2. 마운트 경로: `/app`에만 마운트되어 있으므로 `/health`, `/snapshot` 등과 충돌 없음
3. 경로 분리: API는 루트(`/health`), StaticFiles는 `/app`으로 완전 분리

**검증**:
- `/health` → API 라우트 처리 (StaticFiles 무관)
- `/snapshot` → API 라우트 처리 (StaticFiles 무관)
- `/app/` → StaticFiles 처리 (index.html 반환)
- `/app/index.html` → StaticFiles 처리

### ✅ 권장 마운트 구조

**현재 구조가 올바름**: `/app`에 마운트하는 것이 정확함

**이유**:
- API 라우트와 완전 분리
- PWA 앱은 `/app/`에서 서비스
- Android WebView는 `/app/`에 접근

---

## 3️⃣ Render 배포 환경 점검

### ✅ Uvicorn 실행 커맨드 검증

**현재 설정**:
```bash
uvicorn server.main:app --host 0.0.0.0 --port $PORT
```

**검증 결과**: ✅ **정상**

**이유**:
- `--host 0.0.0.0`: 모든 네트워크 인터페이스에서 수신 (Render 필수)
- `--port $PORT`: Render가 제공하는 환경변수 사용 (정상)
- `server.main:app`: FastAPI 앱 인스턴스 경로 (정상)

### 🔍 Render 로그 해석

**"Your service is live" 이후 404 로그 의미**:

1. **서버는 정상 기동**: Uvicorn이 정상 실행 중
2. **404 발생 가능 시나리오**:
   - 브라우저에서 루트(`/`) 접근 → 라우트 없음 → 404 (예상됨)
   - 잘못된 경로 접근 → 404 (예상됨)
   - **하지만 `/health`, `/snapshot` 등은 200 OK여야 함**

### 🔍 무료 인스턴스 슬립/콜드스타트 영향

**영향**: 
- 첫 요청 시 10-60초 지연 가능 (슬립 해제)
- 하지만 서버 기동 후에는 정상 응답해야 함

**결론**: 슬립은 지연만 발생시키며, 404 원인은 아님

---

## 4️⃣ API 실제 동작 여부 검증 시나리오

| URL | 기대 결과 | 실패 시 가능한 원인 |
|-----|----------|-------------------|
| `GET /health` | 200 OK<br>`{"ok": true, "ts": ..., "owner": "김성훈"}` | 1. 서버 미기동<br>2. 포트 불일치<br>3. 라우트 등록 실패 |
| `GET /snapshot` | 200 OK<br>`{"type": "snapshot", "data": {...}}` | 1. 토큰 불일치 (401)<br>2. 데이터 초기화 전 (빈 데이터)<br>3. 네트워크 오류 |
| `POST /refresh` | 200 OK<br>`{"ok": true, "ts": ...}` | 1. 토큰 불일치 (401)<br>2. 메서드 오류 (GET 사용 시 405) |
| `GET /stock/005930` | 200 OK<br>`{"ok": true, "data": {...}}` | 1. 토큰 불일치 (401)<br>2. 코드 형식 오류 (400)<br>3. 스크래핑 실패 (500) |

### 🔍 각 엔드포인트 상세 분석

#### `/health`
- **인증**: 불필요 (토큰 체크 없음)
- **의존성**: 없음 (즉시 응답)
- **실패 원인**: 서버 미기동 또는 라우팅 오류

#### `/snapshot`
- **인증**: `APP_TOKEN` 설정 시 필수
- **의존성**: `_latest_payload` 전역 변수
- **실패 원인**: 
  - 토큰 불일치 → 401
  - 데이터 미초기화 → 빈 데이터 반환 (200 OK, but empty)

#### `/stock/{code}`
- **인증**: `APP_TOKEN` 설정 시 필수
- **의존성**: 외부 스크래핑 (네이버 금융)
- **실패 원인**:
  - 코드 형식 오류 → 400
  - 스크래핑 실패 → 500
  - 타임아웃 → 500

---

## 5️⃣ Android 앱 연동 관점 점검

### 🔍 WebView 접속 URL

**현재 구조**:
1. Android WebView는 `https://your-app.onrender.com/app/`에 접근
2. StaticFiles가 `index.html` 반환
3. `index.html` 내부의 `app.js`가 API 호출

### 🔍 API 호출 경로 분석

**`mobile/app.js` 코드 분석**:

```javascript
// Line 419-422
const baseUrl = normalizeBaseUrl(localStorage.getItem("ls_server_url") || "");
const snapUrl = httpUrl(baseUrl, "/snapshot");
```

**동작 방식**:
1. `normalizeBaseUrl()`: 저장된 URL 또는 현재 호스트 사용
2. `httpUrl()`: `baseUrl` + `/snapshot` 조합
3. 최종 URL: `https://your-app.onrender.com/snapshot`

### ❌ 문제점 분석

**시나리오 1: 서버 URL 미설정**
```javascript
// baseUrl = "https://your-app.onrender.com" (현재 호스트)
// snapUrl = "https://your-app.onrender.com/snapshot"
// ✅ 정상 작동해야 함
```

**시나리오 2: 서버 URL 잘못 설정**
```javascript
// baseUrl = "https://your-app.onrender.com/app" (잘못된 경로)
// snapUrl = "https://your-app.onrender.com/app/snapshot"
// ❌ 404 발생 (API는 /snapshot에 있음)
```

**시나리오 3: 루트 경로 접근**
```javascript
// WebView가 "https://your-app.onrender.com/" 접근
// ❌ 404 발생 (루트 라우트 없음)
```

### 🔍 데이터가 비어 보이는 근본 원인

**서버 관점**:
1. ✅ API 라우트는 정상 정의됨
2. ✅ `/snapshot`은 정상 작동해야 함
3. ⚠️ 초기 데이터 로딩 전에는 빈 데이터 반환 가능

**클라이언트 관점**:
1. ✅ API 호출 경로는 정확함 (`/snapshot`)
2. ⚠️ 서버 URL 설정 오류 가능성
3. ⚠️ 토큰 불일치 가능성

**분리 분석**:
- **서버 문제**: API 라우트는 정상, 루트 경로만 없음
- **클라이언트 문제**: 서버 URL 설정 또는 토큰 불일치 가능

---

## 6️⃣ 최종 결론 & 수정 가이드

### 🎯 가장 유력한 원인 2가지

#### 원인 1: 루트 경로(`/`) 라우트 없음
**증거**:
- 코드에 `/` 라우트 정의 없음
- 브라우저에서 루트 접근 시 404 발생

**영향**:
- 직접 브라우저 접근 시 혼란
- 하지만 API 엔드포인트는 정상 작동해야 함

#### 원인 2: Android 앱 서버 URL 설정 오류
**증거**:
- `mobile/app.js`가 `localStorage`에서 서버 URL 읽음
- 잘못된 URL 설정 시 API 호출 실패

**영향**:
- `/app/snapshot` 같은 잘못된 경로로 호출
- 404 발생

### 🔧 반드시 수정해야 할 코드

#### 수정 1: 루트 경로 리다이렉트 추가

**위치**: `server/main.py` Line 41 이후

**이유**: 
- 브라우저 접근 시 `/app/`로 자동 리다이렉트
- 사용자 편의성 향상

**코드**:
```python
# Line 41 이후 추가
@app.get("/")
async def root():
    """루트 경로: /app/로 리다이렉트"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/app/", status_code=302)
```

#### 수정 2: API 문서 경로 추가 (선택사항)

**위치**: `server/main.py` Line 32

**이유**: 
- FastAPI 자동 문서 접근 가능
- 디버깅 편의성

**코드**:
```python
# Line 32 수정
app = FastAPI(
    title="LeadingStock API",
    version="0.1.0",
    docs_url="/docs",  # 추가
    redoc_url="/redoc"  # 추가
)
```

### ✅ 정상 동작이 보장되는 최종 구조 예시 코드

```python
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
# Health Check
# --------------------------------------------------
@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "ts": int(time.time()),
        "owner": OWNER_NAME,
    })

# ... 나머지 라우트는 기존과 동일 ...
```

### 📋 수정 → Git Commit → Render 재배포 순서

#### 1단계: 코드 수정
```bash
# server/main.py 수정
# - 루트 경로 리다이렉트 추가
# - FastAPI docs_url, redoc_url 추가 (선택)
```

#### 2단계: 로컬 테스트
```bash
# 서버 실행
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000

# 테스트
curl http://localhost:8000/health
curl http://localhost:8000/
curl http://localhost:8000/snapshot
```

#### 3단계: Git Commit
```bash
git add server/main.py
git commit -m "fix: 루트 경로 리다이렉트 추가 및 API 문서 경로 설정"
git push origin main
```

#### 4단계: Render 재배포
- Render 대시보드에서 자동 배포 확인
- 또는 수동으로 "Manual Deploy" 실행

#### 5단계: 배포 후 검증
```bash
# 1. Health Check
curl https://your-app.onrender.com/health

# 2. 루트 경로 리다이렉트 확인
curl -I https://your-app.onrender.com/

# 3. API 엔드포인트 확인
curl https://your-app.onrender.com/snapshot

# 4. PWA 앱 접근
curl https://your-app.onrender.com/app/
```

---

## ✅ 검증 체크리스트

### 서버 측 검증
- [ ] `GET /health` → 200 OK
- [ ] `GET /` → 302 Redirect to `/app/`
- [ ] `GET /app/` → 200 OK (index.html)
- [ ] `GET /snapshot` → 200 OK (데이터 또는 빈 데이터)
- [ ] `GET /docs` → 200 OK (FastAPI 문서)

### 클라이언트 측 검증
- [ ] Android 앱에서 서버 URL이 올바르게 설정됨
- [ ] `localStorage.getItem("ls_server_url")` 값 확인
- [ ] 토큰 설정 시 `APP_TOKEN` 일치 확인
- [ ] 네트워크 탭에서 실제 API 호출 URL 확인

### Render 배포 검증
- [ ] Render 로그에서 "Uvicorn running on" 확인
- [ ] Render 로그에서 404가 아닌 200/302 응답 확인
- [ ] 서비스 상태가 "Live"인지 확인

---

## 🎯 최종 요약

### 문제 원인
1. **루트 경로(`/`) 라우트 없음**: 브라우저 접근 시 404 발생
2. **Android 앱 서버 URL 설정 오류 가능성**: 잘못된 경로로 API 호출

### 해결 방법
1. **루트 경로 리다이렉트 추가**: `/` → `/app/` 자동 리다이렉트
2. **API 문서 경로 추가**: 디버깅 편의성 향상
3. **Android 앱 서버 URL 확인**: 올바른 도메인 설정 확인

### 예상 결과
- ✅ 브라우저에서 루트 접근 시 자동으로 PWA 앱으로 이동
- ✅ API 엔드포인트 정상 작동
- ✅ Android 앱에서 데이터 정상 수신

---

**작성일**: 2025-01-23
**분석 기준**: `server/main.py` Line 1-260, `mobile/app.js` 전체, Render 배포 설정

