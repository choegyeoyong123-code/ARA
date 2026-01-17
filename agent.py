import os
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history
from tools import TOOLS_SPEC, search_kmou_web, search_campus_knowledge, get_user_profile

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    user_name = await get_user_profile(user_id)

    # [보안] 대화 순서 꼬임 방지 (400 에러 해결)
    if history and history[0].get("role") in ["tool", "assistant"]: history = []

    if not history:
        history.append({"role": "system", "content": f"너는 한국해양대 AI 아라다. 사용자는 {user_name} 선장님이다. [규칙] 1. 제공된 도구 결과로만 답변할 것. 2. 절대 추측하지 말 것. 3. 3줄 이내 존댓말로 답할 것."})
    
    history.append({"role": "user", "content": user_input})

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=history, tools=TOOLS_SPEC, temperature=0
        )
        msg = response.choices[0].message
        
        if msg.tool_calls:
            history.append(msg.model_dump())
            tasks = []
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                if tc.function.name == "search_kmou_web": tasks.append(search_kmou_web(args['query']))
                elif tc.function.name == "search_campus_knowledge": tasks.append(search_campus_knowledge(args['query']))
            
            results = await asyncio.gather(*tasks)
            for tc, res in zip(msg.tool_calls, results):
                history.append({"tool_call_id": tc.id, "role": "tool", "name": tc.function.name, "content": str(res)})
            
            final = await client.chat.completions.create(model="gpt-4o-mini", messages=history, temperature=0)
            answer = final.choices[0].message.content
        else:
            answer = msg.content

        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history)
        return answer
    except Exception as e:
        print(f"🚨 Agent Error: {e}")
        return "데이터 분석 중 작은 파도가 쳤습니다. 🌊 잠시 후 다시 시도해 주세요!"