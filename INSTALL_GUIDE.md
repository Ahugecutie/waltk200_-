# 필수 프로그램 설치 가이드

## 1. Node.js 설치 (필수)

### 다운로드 및 설치
1. **Node.js 공식 사이트 방문**
   - https://nodejs.org/
   - 또는 직접 링크: https://nodejs.org/dist/v20.11.0/node-v20.11.0-x64.msi

2. **LTS 버전 다운로드** (권장)
   - "LTS" 버전 선택 (현재: v20.x.x)
   - Windows 64-bit 설치 파일 (.msi) 다운로드

3. **설치 실행**
   - 다운로드한 `.msi` 파일 실행
   - "Next" 클릭하여 기본 설정으로 설치
   - ✅ "Automatically install the necessary tools" 체크 (선택사항)
   - 설치 완료 후 **컴퓨터 재시작** (권장)

4. **설치 확인**
   - PowerShell 또는 명령 프롬프트 열기
   - 다음 명령어 실행:
   ```bash
   node --version
   npm --version
   ```
   - 버전 번호가 표시되면 설치 완료!

---

## 2. Android Studio 설치 (필수)

### 다운로드 및 설치
1. **Android Studio 공식 사이트 방문**
   - https://developer.android.com/studio
   - 또는 직접 링크: https://redirector.gvt1.com/edgedl/android/studio/install/2023.2.1.25/android-studio-2023.2.1.25-windows.exe

2. **다운로드**
   - "Download Android Studio" 버튼 클릭
   - 약 1GB 정도의 설치 파일 다운로드

3. **설치 실행**
   - 다운로드한 `.exe` 파일 실행
   - "Next" 클릭하여 기본 설정으로 설치
   - ✅ **중요**: "Android SDK", "Android SDK Platform", "Android Virtual Device" 모두 체크되어 있는지 확인
   - 설치 경로는 기본값 사용 권장
   - 설치 완료 후 Android Studio 실행

4. **초기 설정 (첫 실행 시)**
   - "Setup Wizard" 화면에서:
     - "Standard" 설치 선택
     - SDK 다운로드 및 설정 자동 진행 (시간 소요: 10-30분)
   - 완료 후 Android Studio 메인 화면이 나타나면 준비 완료!

5. **설치 확인**
   - Android Studio 실행
   - 상단 메뉴: `Tools` → `SDK Manager`
   - "Android SDK" 탭에서 SDK 설치 여부 확인

---

## 3. 설치 후 다음 단계

설치가 완료되면 다음 명령어를 실행하세요:

```bash
# 1. 프로젝트 폴더로 이동
cd "C:\Users\PC\Desktop\주식관련\주도주탐색기"

# 2. Node.js 패키지 설치
npm install

# 3. Capacitor Android 플러그인 추가
npx cap add android

# 4. Android 프로젝트 동기화
npx cap sync

# 5. Android Studio 열기
npx cap open android
```

---

## 문제 해결

### Node.js 설치 후 명령어가 안 될 때
- 컴퓨터 재시작
- PowerShell을 **관리자 권한**으로 실행
- 환경 변수 확인: `echo $env:PATH`

### Android Studio SDK 다운로드 실패
- 인터넷 연결 확인
- 방화벽/프록시 설정 확인
- SDK Manager에서 수동 다운로드 시도

### 설치 중 오류 발생
- 관리자 권한으로 실행
- 바이러스 백신 프로그램 일시 중지
- 디스크 공간 확인 (최소 10GB 여유 공간 필요)

---

## 설치 완료 확인 체크리스트

- [ ] `node --version` 명령어 실행 시 버전 표시
- [ ] `npm --version` 명령어 실행 시 버전 표시
- [ ] Android Studio 실행 및 메인 화면 표시
- [ ] SDK Manager에서 Android SDK 설치 확인

모든 항목이 체크되면 APK 빌드를 진행할 수 있습니다!

