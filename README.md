## 목적
PC(서버)가 켜져 있을 때는 실시간(1초 단위) 데이터를 모바일로 푸시하고, **PC가 꺼져 있으면 모바일 UI에**  
**`김성훈님에 컴퓨터가 꺼져있습니다`** 문구가 뜨도록 구성합니다.

## 구성
- `server/main.py`: FastAPI 서버 (`/health`, `/ws`)
- `mobile/`: 설치형 PWA(오프라인에서도 열림). 서버 연결이 끊기면 위 문구 표시.

## 실행(PC)
PowerShell에서 프로젝트 루트 기준:

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r server\\requirements.txt
$env:OWNER_NAME="김성훈"
# 선택: 접속 토큰 (모바일에서 동일 토큰 입력)
$env:APP_TOKEN="your_token"
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

이후 브라우저/모바일에서:
- `http://PC_IP:8000/` 접속 → PWA 설치(“홈 화면에 추가”)
- 서버가 꺼지면 앱은 열리지만 연결 실패로 문구가 표시됩니다.

## 무료 PaaS(슬립 허용) 배포
전제: **오랜 무접속 후 첫 갱신이 10~60초 느려져도 OK**인 경우에 적합합니다.

### Render 예시
- 이 저장소를 GitHub에 올린 뒤 Render에서 “New Web Service”로 연결
- Render 설정:
  - **Build Command**: `pip install -r server/requirements.txt`
  - **Start Command**: `uvicorn server.main:app --host 0.0.0.0 --port $PORT`
  - **Env**:
    - `OWNER_NAME=김성훈`
    - (선택) `APP_TOKEN=원하는토큰`
    - `AUTO_REFRESH_SEC=60`

배포 후 주소가 예를 들어 `https://xxxx.onrender.com`이면:
- 모바일에서 접속 후 PWA 설치
- 앱의 “서버 주소”에 `https://xxxx.onrender.com` 입력

### 컨테이너 기반 PaaS(범용)
`Dockerfile`이 포함되어 있어, Docker 배포를 지원하는 PaaS라면 동일하게 올릴 수 있습니다.



