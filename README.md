# 🌊 ARA V3.0: 한국해양대 라이프스타일 에이전트

**ARA(아라)**는 한국해양대학교(KMOU) 학생들의 캠퍼스 라이프를 혁신하기 위해 설계된 **API 기반 초지능형 AI 비서**입니다.
복잡한 로컬 연산 없이, 검증된 **공공데이터포털(Data.go.kr)**의 실시간 API 5종을 OpenAI GPT-4o-mini와 결합하여 환각(Hallucination) 없는 정확한 정보를 제공합니다.

---

## 🚀 Key Features (핵심 기능)

ARA는 추측하지 않습니다. 부산시와 기상청의 실시간 데이터를 직접 조회하여 답변합니다.

1.  **🚌 Mobility (이동 관제)**: 190번, 101번, 88번 등 교내 진입 시내버스의 **실시간 위치 및 도착 예정 시간** 조회 (부산버스정보시스템).
2.  **🍱 Dining (생존 식사)**: 영도구 내 **착한가격업소(가성비 식당)** 및 맛집 추천.
3.  **💊 Safety (의료 안전)**: 학교 근처 현재 영업 중인 **약국 및 병원** 실시간 조회.
4.  **🌤️ Environment (기상 대응)**: 해양대 캠퍼스(동삼동)의 **초단기 기상 실황** (기온, 강수 형태) 조회.
5.  **🎉 Leisure (문화 여가)**: 부산시 내 개최 예정인 **축제 및 행사 정보** 브리핑.

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