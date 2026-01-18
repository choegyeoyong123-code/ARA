import os
import json
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history
from tools import TOOLS_SPEC, get_weather_real, search_kmou_web, search_campus_knowledge, get_user_profile

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 도구 이름과 함수 매핑 (확장성 확보)
TOOL_MAP = {
    "get_weather_real": get_weather_real,
    "search_kmou_web": search_kmou_web,
    "search_campus_knowledge": search_campus_knowledge
}

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    user_profile = await get_user_profile(user_id) # 프로필 확장 데이터 가정

    # 1. 초지능형 페르소나 주입 (System Prompt Engineering)
    if not history or history[0].get("role") != "system":
        system_logic = (
            f"당신은 한국해양대학교의 초지능 AI 에이전트 '아라(ARA)'입니다. "
            f"사용자는 '{user_profile}' 선장님입니다. "
            "단순 정보 전달을 넘어, 데이터를 분석하여 선장님의 시간을 아껴주는 '캠퍼스 전략'을 제시하십시오. "
            "1. 모든 답변은 근거(도구 결과)에 기반하며, 추측하지 않습니다. "
            "2. 답변 끝에는 항상 [데이터 출처]를 명시하십시오. "
            "3. 복잡한 정보는 구조화된 리스트나 테이블을 활용하여 가독성을 극대화하십시오."
        )
        history = [{"role": "system", "content": system_logic}]
    
    history.append({"role": "user", "content": user_input})

    try:
        # Step 1: 의도 파악 및 도구 호출 결정
        response = await client.chat.completions.create(
            model="gpt-4o-mini", # 속도 최적화, 필요시 gpt-4o로 업그레이드
            messages=history, 
            tools=TOOLS_SPEC, 
            tool_choice="auto",
            temperature=0.1 # 일관성 있는 분석을 위해 낮게 설정
        )
        msg = response.choices[0].message
        
        # Step 2: 병렬 도구 실행 (Parallel Execution)
        if msg.tool_calls:
            history.append(msg.model_dump(exclude_none=True))
            tasks = []
            for tc in msg.tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)
                
                # TOOL_MAP을 통한 동적 실행 (if-elif 노가다 제거)
                if func_name in TOOL_MAP:
                    func = TOOL_MAP[func_name]
                    # 인자값이 없는 함수와 있는 함수 구분 처리
                    tasks.append(func(**args) if args else func())

            results = await asyncio.gather(*tasks)

            for tc, res in zip(msg.tool_calls, results):
                history.append({
                    "tool_call_id": tc.id, 
                    "role": "tool", 
                    "name": tc.function.name, 
                    "content": str(res)
                })
            
            # Step 3: 데이터 기반 최종 추론
            final_res = await client.chat.completions.create(
                model="gpt-4o-mini", 
                messages=history,
                temperature=0.3
            )
            answer = final_res.choices[0].message.content
        else:
            answer = msg.content

        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history[-10:]) # 최근 10개 대화로 컨텍스트 최적화(토큰 절약)
        return answer

    except Exception as e:
        print(f"🚨 Agent Error: {e}")
        return "데이터 엔진에 일시적인 파도가 높습니다. 다시 명령을 내려주십시오, 선장님! 🌊"