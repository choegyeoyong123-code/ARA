# 🌊 ARA V3.0: 한국해양대 라이프스타일 에이전트

**ARA(아라)**는 한국해양대학교(KMOU) 학생들의 캠퍼스 라이프를 혁신하기 위해 설계된 **API 기반 초지능형 AI 비서**입니다.
복잡한 로컬 연산 없이, 검증된 **공공데이터포털(Data.go.kr)**의 실시간 API 5종을 OpenAI GPT-4o-mini와 결합하여 환각(Hallucination) 없는 정확한 정보를 제공합니다.

---

## 🚀 Key Features (핵심 기능)

ARA는 추측하지 않습니다. 부산시와 기상청의 실시간 데이터를 직접 조회하여 답변합니다.

1.  **🚌 Mobility (이동 관제)**: 190번, 101번, 88번 등 시내버스 **정류장 실시간 도착 정보** 조회 (ODsay 기반).
2.  **🍱 Dining (생존 식사)**: 영도구 내 **착한가격업소(가성비 식당)** 및 맛집 추천.
3.  **💼 Career (취업)**: 온통청년/Work24 기반 공고를 카드 UI로 빠르게 확인.
4.  **🌤️ Environment (기상 대응)**: 해양대 캠퍼스(동삼동)의 **초단기 기상 실황** (기온, 강수 형태) 조회.
5.  **📌 Admin (제보 관리)**: 사용자 맛집 제보를 DB에 저장하고 관리자 페이지에서 검수/승인.

---

## 🛠️ Tech Stack (기술 스택)

최소한의 리소스로 최대의 성능을 내는 **Serverless-Ready** 아키텍처입니다.

* **Core**: Python 3.10+
* **Web Framework**: FastAPI (Asynchronous)
* **AI Engine**: OpenAI GPT-4o-mini (with Function Calling)
* **API Client**: HTTPX (Non-blocking Async Request)
* **Frontend**: HTML5 / Vanilla JS / Jinja2 Templates (Responsive Dock UI)
* **Deployment**: Render / Docker

---

## 📂 Project Structure (

## 🔐 Environment Variables (필수/선택)

아래 값들은 **반드시 환경 변수로 관리**해야 하며, 코드에 하드코딩하지 않습니다.

- **필수**
  - `OPENAI_API_KEY`: OpenAI API Key
  - `ODSAY_API_KEY`: ODsay API Key (버스 기능)
- **선택(설정 시 기능 활성화)**
  - `DATA_GO_KR_SERVICE_KEY`: 공공데이터포털 서비스키(날씨/의료/착한가격업소 등)
  - `PORT`: 서버 포트(기본 8000)
  - `ARA_CACHE_TTL_SECONDS`: 외부 API 캐시 TTL(기본 60초)
  - `ARA_HTTPX_VERIFY`: TLS 인증서 검증 여부(`true`/`false`, 기본 `false`)

## 🧭 Bus Tracking (Ocean View)

버스 안내는 OUT/IN 방향을 **명시 입력**으로 받습니다.

- **OUT(진출)**: 구본관 → 방파제입구 → 승선생활관
- **IN(진입)**: 승선생활관 → 대학본부 → 구본관

예시:
- `190 OUT 버스 도착 정보 알려줘`
- `101 IN 버스 언제 와?`

## 🚀 Deployment (배포)

### Render.com 배포 시

**Start Command**를 다음으로 설정하여 다중 워커로 동시 사용자 처리 성능을 향상시킵니다:

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

- `-w 4`: 4개의 워커 프로세스 생성 (동시 요청 처리)
- `-k uvicorn.workers.UvicornWorker`: Uvicorn 워커 사용 (비동기 지원)
- `main:app`: FastAPI 애플리케이션 진입점

### 성능 최적화

- 모든 외부 API 호출은 `httpx.AsyncClient`를 사용하여 비동기 처리됩니다.
- 캐시는 전역 딕셔너리로 관리되며, `asyncio.Lock`으로 동시 접근을 안전하게 처리합니다.
- API 타임아웃은 3.0초로 고정되어 KakaoTalk의 5초 제한을 준수합니다.

## 📚 Docs Indexing (RAG 성능 최적화)

Cursor의 Docs/Indexing 기능에 아래 공식 문서를 추가하여, 스킬 서버(JSON) 규격 및 API 사용 지침을 빠르게 참조할 수 있도록 합니다.

- Kakao i OpenBuilder: `https://kakao-i-openbuilder.io/docs`
- OpenAI API: `https://platform.openai.com/docs`
- Telegram Bot API: `https://core.telegram.org/bots/api`