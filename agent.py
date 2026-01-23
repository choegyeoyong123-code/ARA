import os
import sys
import logging
import traceback
import json
import datetime
from pathlib import Path
from dotenv import load_dotenv
from openai import AsyncOpenAI

# 1. 환경 설정 로드
load_dotenv()

# 2. 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ARA_Agent")

# 3. 데이터 경로 설정
current_dir = Path(__file__).parent.absolute()
data_dir = current_dir / "university_data"

# 4. OpenAI Client 초기화
api_key = os.getenv("OPENAI_API_KEY")
client = None
if api_key:
    client = AsyncOpenAI(api_key=api_key)
else:
    logger.error("❌ OPENAI_API_KEY Missing!")

# ==========================================
# [Tool] 파일 직접 읽기 도구 (RAG)
# ==========================================
def read_text_file(filename: str) -> str:
    """
    university_data 폴더 내의 특정 텍스트 파일을 읽어옵니다.
    """
    try:
        file_path = data_dir / f"{filename}.txt"
        if not file_path.exists():
            return "해당 정보에 대한 데이터 파일이 아직 수집되지 않았습니다."
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                return "데이터 파일이 비어 있습니다."
            return content[:2000] # 토큰 제한을 위해 앞부분 2000자만 리턴
    except Exception as e:
        return f"파일 읽기 중 오류 발생: {str(e)}"

# ==========================================
# [Tool] 도구 정의 (OpenAI Function Calling)
# ==========================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_university_info",
            "description": "학교 생활 정보(학식, 공지사항, 학사일정 등)를 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["cafeteria_menu", "notice_general", "academic_guide", "scholarship_guide", "events_seminar"],
                        "description": "조회할 정보의 카테고리 (학식, 공지, 학사, 장학, 행사)"
                    }
                },
                "required": ["category"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "현재 날짜와 시간을 조회합니다. (요일 확인 등)",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# ==========================================
# 핵심 LLM 호출 함수 (Tool Execution 포함)
# ==========================================
async def ask_ara(user_query: str) -> str:
    if client is None:
        return "죄송합니다. 서버 설정 오류입니다."

    messages = [
        {
            "role": "system", 
            "content": (
                "당신은 국립한국해양대학교 학사 도우미 'ARA'입니다. "
                "사용자의 질문에 맞는 도구(get_university_info)를 사용하여 정보를 조회한 뒤 답변하세요. "
                "정보가 없으면 솔직하게 모른다고 답하세요. "
                "답변은 400자 이내로 친절하게 요약하세요."
            )
        },
        {"role": "user", "content": user_query}
    ]

    try:
        # 1차 호출: 도구 사용 여부 판단
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.2
        )
        
        response_msg = response.choices[0].message
        tool_calls = response_msg.tool_calls

        # 도구를 사용하라고 함!
        if tool_calls:
            # 대화 내역에 AI의 '도구 사용 요청' 추가
            messages.append(response_msg)

            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                tool_result = ""
                
                # [Action] 도구 실행
                if function_name == "get_university_info":
                    category = function_args.get("category")
                    logger.info(f"🔍 [Tool] 파일 조회 시도: {category}")
                    tool_result = read_text_file(category)
                    
                elif function_name == "get_current_time":
                    now = datetime.datetime.now()
                    tool_result = now.strftime("%Y년 %m월 %d일 %H시 %M분 (%A)")

                # 도구 결과를 메시지에 추가 (role: tool)
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": tool_result
                })

            # 2차 호출: 도구 결과(Context)를 보고 최종 답변 생성
            final_response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7
            )
            return final_response.choices[0].message.content
        
        # 도구 사용 안 함 (일반 대화)
        else:
            return response_msg.content

    except Exception as e:
        logger.error(f"❌ ask_ara 오류: {e}")
        logger.error(traceback.format_exc())
        return "죄송합니다. 정보를 처리하는 중 오류가 발생했습니다."

# ==========================================
# 카카오톡 연동 메인 함수
# ==========================================
DISCLAIMER_TEXT = (
    "\n\n━━━━━━━━━━━━━━\n"
    "⚠️ [면책 고지] 본 답변은 AI 자동 생성 정보입니다. "
    "정확한 내용은 학교 홈페이지를 확인하세요."
)

async def process_query(user_utterance: str) -> dict:
    try:
        logger.info(f"🤖 [Agent] 질문: {user_utterance}")
        
        # 답변 생성
        answer_text = await ask_ara(user_utterance)
        
        # 면책 조항 결합
        final_answer = str(answer_text) + DISCLAIMER_TEXT

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {"text": final_answer}
                    }
                ]
            }
        }
    except Exception as e:
        logger.error(f"❌ [Agent] Fatal: {e}")
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {"text": "시스템 오류가 발생했습니다."}
                    }
                ]
            }
        }