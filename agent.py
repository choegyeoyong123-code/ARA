import os
import json
import asyncio
from openai import AsyncOpenAI
from tools import TOOLS_SPEC, get_bus_arrival, get_cheap_eats, get_medical_info, get_kmou_weather, get_festival_info

client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

TOOL_MAP = {
    "get_bus_arrival": get_bus_arrival,
    "get_cheap_eats": get_cheap_eats,
    "get_medical_info": get_medical_info,
    "get_kmou_weather": get_kmou_weather,
    "get_festival_info": get_festival_info
}

async def ask_ara(user_input, history=[]):
    # [IQ 140 System Prompt]
    system_prompt = {
        "role": "system", 
        "content": (
            "당신은 **논리적 추론 엔진**을 탑재한 한국해양대 AI 'ARA'입니다.\n"
            "사용자의 질문에 답하기 위해 반드시 아래 **4단계 사고 과정(Chain of Thought)**을 거쳐야 합니다.\n\n"
            
            "**[Phase 1: Fact Gathering (사실 수집)]**\n"
            "- 사용자의 의도를 파악하고 필요한 API 도구를 모두 호출하세요.\n"
            "- *중요:* 맛집/축제/외출 관련 질문에는 항상 `get_kmou_weather`를 함께 호출하여 날씨 정보를 확보하세요.\n\n"
            
            "**[Phase 2: Data Verification (데이터 검증)]**\n"
            "- API가 반환한 JSON 데이터를 분석하세요.\n"
            "- `status`가 'error'이거나 'empty'라면, 절대 데이터를 지어내지 말고 '정보가 없다'고 실토하세요. (환각 방지)\n"
            "- 버스 도착 시간이 '50분' 이상이면 '차고지 대기 중일 수 있음'을 추론하세요.\n\n"
            
            "**[Phase 3: Contextual Reasoning (맥락 추론)]**\n"
            "- **(날씨 + 이동)**: 비가 오면(rain_type > 0) 버스 배차 간격이 늘어질 수 있음을 경고하세요.\n"
            "- **(날씨 + 식사)**: 풍속이 10m/s 이상이면 '배달'이나 '가까운 식당'을 우선 추천하세요.\n"
            "- **(버스 + 위치)**: 버스 위치가 5정거장 이내면 '곧 도착하니 뛰어가세요'라고 조언하세요.\n\n"
            
            "**[Phase 4: Final Output (최종 답변)]**\n"
            "- 추론 과정은 내부적으로만 수행하고, 사용자에게는 결론만 간결하고 친절하게(이모지 포함) 전달하세요.\n"
            "- 모든 주장의 근거(데이터)를 명확히 하세요."
        )
    }
    
    messages = [system_prompt] + history + [{"role": "user", "content": user_input}]

    try:
        # 1. 도구 호출 결정 (Reasoning Start)
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, tools=TOOLS_SPEC, tool_choice="auto"
        )
        msg = response.choices[0].message

        # 2. 도구 실행
        if msg.tool_calls:
            messages.append(msg)
            tasks = []
            for tc in msg.tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)
                if func_name in TOOL_MAP:
                    tasks.append(TOOL_MAP[func_name](**args) if args else TOOL_MAP[func_name]())
            
            # 병렬 실행으로 속도 확보
            results = await asyncio.gather(*tasks)

            # 3. 도구 결과 주입 (Raw JSON Data)
            for tc, res in zip(msg.tool_calls, results):
                messages.append({
                    "tool_call_id": tc.id, "role": "tool", 
                    "name": tc.function.name, "content": str(res)
                })

            # 4. 최종 추론 및 답변 생성
            final_res = await client.chat.completions.create(
                model="gpt-4o-mini", messages=messages, temperature=0.3 # 환각 방지를 위해 창의성(Temperature) 낮춤
            )
            return final_res.choices[0].message.content
        
        # 도구 호출이 필요 없는 일반 대화
        return msg.content

    except Exception as e:
        print(f"System Logic Error: {e}")
        return "죄송합니다. 논리 회로를 재구성 중입니다. 잠시 후 다시 시도해 주세요."