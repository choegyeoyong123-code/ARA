# KMOU Chatbot

한국해양대학교(KMOU)를 위한 특수 챗봇 시스템입니다. FastAPI와 LangGraph를 사용하여 실시간 셔틀 정보와 일반 지원을 제공합니다.

## 주요 기능

- 🤖 **의도 기반 라우팅**: 사용자 질문을 자동으로 분류하여 셔틀 정보 또는 일반 지원으로 라우팅
- 🚌 **실시간 셔틀 정보**: 셔틀 버스의 실시간 위치, 시간표, 다음 출발 시간 조회
- 💬 **일반 챗봇**: 학교 정보, 학사 일정, 생활 정보 등에 대한 질문 답변
- 🔄 **대화 기록 관리**: 대화 컨텍스트를 유지하여 자연스러운 대화 가능

## 기술 스택

- **FastAPI**: 고성능 비동기 웹 프레임워크
- **LangGraph**: 복잡한 대화 흐름 관리
- **LangChain**: LLM 통합 및 프롬프트 관리
- **OpenAI GPT**: 자연어 처리 및 응답 생성
- **Pydantic**: 데이터 검증 및 모델링

## 설치 및 실행

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

`.env` 파일을 생성하고 다음 내용을 추가하세요:

```env
OPENAI_API_KEY=your_openai_api_key_here
API_HOST=0.0.0.0
API_PORT=8000
SHUTTLE_API_URL=https://api.example.com/shuttle  # 선택사항
SHUTTLE_API_KEY=your_shuttle_api_key_here  # 선택사항
```

### 3. 서버 실행

```bash
python main.py
```

또는 uvicorn을 직접 사용:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 4. API 문서 확인

브라우저에서 `http://localhost:8000/docs`를 열어 Swagger UI에서 API를 테스트할 수 있습니다.

## API 사용 예시

### 챗봇과 대화하기

```bash
curl -X POST "http://localhost:8000/chat/" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "셔틀 버스 시간표 알려줘",
    "conversation_id": "user123"
  }'
```

### 응답 예시

```json
{
  "response": "셔틀 버스 시간표는 다음과 같습니다:\n- 본관-기숙사: 08:00, 08:30, 09:00...\n- 기숙사-본관: 08:15, 08:45, 09:15...",
  "conversation_id": "user123",
  "intent": "shuttle",
  "metadata": {
    "intent": "shuttle",
    "realtime_info": [...],
    "schedules": [...]
  }
}
```

## 프로젝트 구조

```
kmou_bot/
├── main.py                 # FastAPI 메인 애플리케이션
├── requirements.txt        # Python 의존성
├── .env                    # 환경 변수 (생성 필요)
├── models/                 # 데이터 모델
│   ├── __init__.py
│   ├── message.py          # 메시지 모델
│   └── shuttle.py          # 셔틀 정보 모델
├── services/               # 비즈니스 로직
│   ├── __init__.py
│   ├── intent_classifier.py    # 의도 분류기
│   ├── shuttle_service.py      # 셔틀 서비스
│   └── chat_service.py         # 일반 챗봇 서비스
├── graph/                  # LangGraph 그래프
│   ├── __init__.py
│   └── chat_graph.py       # 대화 흐름 그래프
└── routers/                # API 라우터
    ├── __init__.py
    └── chat.py             # 챗봇 라우터
```

## 작동 원리

1. **의도 분류**: 사용자 메시지가 들어오면 `IntentClassifier`가 "shuttle" 또는 "general"로 분류합니다.

2. **라우팅**: LangGraph의 조건부 엣지를 사용하여 의도에 따라 적절한 핸들러로 라우팅됩니다.

3. **응답 생성**:
   - **셔틀 질문**: `ShuttleService`에서 실시간 정보를 조회하고, LLM이 자연스러운 응답을 생성합니다.
   - **일반 질문**: `ChatService`가 대화 기록을 유지하며 일반적인 질문에 답변합니다.

## 커스터마이징

### 셔틀 API 연동

`services/shuttle_service.py`의 `get_realtime_info()` 메서드를 수정하여 실제 셔틀 API와 연동할 수 있습니다.

### 프롬프트 수정

- 셔틀 응답: `graph/chat_graph.py`의 `handle_shuttle()` 메서드
- 일반 응답: `services/chat_service.py`의 `system_prompt`

## 라이선스

MIT License
