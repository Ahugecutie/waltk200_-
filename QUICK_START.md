# 빠른 시작 가이드

## 설치가 완료되었다면

### 1단계: 의존성 설치
```bash
npm install
```

### 2단계: Capacitor 초기화
```bash
npx cap add android
npx cap sync
```

### 3단계: Android Studio에서 빌드
```bash
npx cap open android
```

Android Studio가 열리면:
1. 상단 메뉴: `Build` → `Build Bundle(s) / APK(s)` → `Build APK(s)`
2. 빌드 완료 후 알림 확인
3. `android/app/build/outputs/apk/debug/app-debug.apk` 파일 생성됨

### 4단계: APK 설치
- 생성된 `app-debug.apk` 파일을 안드로이드 폴더로 전송
- 안드로이드 폴더에서 파일 실행하여 설치
- "알 수 없는 출처" 허용 필요 (설정 → 보안)

### 5단계: 앱 사용
1. 앱 실행
2. 상단 설정 버튼(⚙) 클릭
3. 서버 URL 입력 (예: `https://your-render-app.onrender.com`)
4. 저장 후 사용

---

## 전체 과정 요약

```
Node.js 설치 → Android Studio 설치 → npm install → npx cap add android → 
npx cap sync → npx cap open android → Build APK → 설치 → 사용
```

예상 소요 시간: **30분 ~ 1시간** (설치 시간 포함)

