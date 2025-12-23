# APK 빌드 가이드

## 사전 요구사항

1. **Node.js 설치** (v16 이상)
   - https://nodejs.org/ 에서 다운로드

2. **Android Studio 설치**
   - https://developer.android.com/studio
   - Android SDK 설치 필수

3. **Java JDK 11 이상**
   - Android Studio 설치 시 포함됨

## 설치 및 빌드 단계

### 1. 의존성 설치
```bash
npm install
```

### 2. Capacitor 초기화
```bash
npx cap add android
```

### 3. Android 프로젝트 생성
```bash
npx cap sync
```

### 4. Android Studio에서 열기
```bash
npx cap open android
```

또는 직접:
```bash
# Android Studio에서 android 폴더 열기
```

### 5. Android Studio에서 APK 빌드

1. Android Studio 실행
2. `android` 폴더 열기
3. 상단 메뉴: `Build` → `Build Bundle(s) / APK(s)` → `Build APK(s)`
4. 빌드 완료 후: `android/app/build/outputs/apk/debug/app-debug.apk` 생성됨

### 6. APK 설치

생성된 APK 파일을 안드로이드 폴더로 전송하여 설치

## 서버 URL 설정

앱 설치 후:
1. 앱 실행
2. 상단 설정 버튼(⚙) 클릭
3. 서버 URL 입력 (예: `https://your-render-app.onrender.com`)
4. 저장

## 주의사항

- **디버그 APK**: 개발/테스트용 (서명 없음)
- **릴리즈 APK**: 배포용 (서명 필요, Google Play 등록 시)

## 릴리즈 APK 빌드 (선택사항)

1. Android Studio에서: `Build` → `Generate Signed Bundle / APK`
2. 키스토어 생성 또는 기존 키스토어 사용
3. APK 선택 후 빌드

## 문제 해결

### "command not found: npx"
- Node.js가 설치되지 않았거나 PATH에 없음
- Node.js 재설치 또는 PATH 설정 확인

### "Android SDK not found"
- Android Studio에서 SDK Manager 열기
- Android SDK Platform 설치 확인

### 빌드 에러
- `npx cap sync` 재실행
- Android Studio에서 `File` → `Invalidate Caches / Restart`

