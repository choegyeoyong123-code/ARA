import os
import json
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history
from tools import TOOLS_SPEC, get_weather_real, search_kmou_web, search_campus_knowledge, get_user_profile

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    user_name = await get_user_profile(user_id)

    if not history or history[0].get("role") != "system":
        history = [{"role": "system", "content": f"너는 해양대 AI 아라다. 사용자는 {user_name} 선장님이다. 도구 결과로만 3줄 이내 답하라."}]
    
    history.append({"role": "user", "content": user_input})

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=history, tools=TOOLS_SPEC, temperature=0
        )
        msg = response.choices[0].message
        
        if msg.tool_calls:
            history.append(msg.model_dump(exclude_none=True)) # 직렬화 해결
            tasks = []
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                q = args.get('query') or user_input # KeyError 방어

                if tc.function.name == "get_weather_real": tasks.append(get_weather_real())
                elif tc.function.name == "search_kmou_web": tasks.append(search_kmou_web(q))
                elif tc.function.name == "search_campus_knowledge": tasks.append(search_campus_knowledge(q))

            results = await asyncio.gather(*tasks)
            for tc, res in zip(msg.tool_calls, results):
                history.append({"tool_call_id": tc.id, "role": "tool", "name": tc.function.name, "content": str(res)})
            
            final_res = await client.chat.completions.create(model="gpt-4o-mini", messages=history)
            answer = final_res.choices[0].message.content
        else:
            answer = msg.content

        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history)
        return answer
    except Exception as e:
        print(f"🚨 Agent Error: {e}")
        return "통신이 불안정합니다. 잠시 후 다시 시도해주세요! 🌊"

        import os
import uvicorn
from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

@app.on_event("startup")
def startup():
    init_db()

def get_quick_replies():
    return [
        {"label": "🌡️ 실시간 날씨", "action": "message", "messageText": "지금 학교 날씨 어때?"},
        {"label": "🌐 공지사항 검색", "action": "message", "messageText": "최근 장학금 공지 알려줘"},
        {"label": "📞 전화번호 찾기", "action": "message", "messageText": "학생처 전화번호 알려줘"}
    ]

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    try:
        payload = await request.json()
        u_id = payload['userRequest']['user']['id']
        text = payload['userRequest']['utterance']
        
        response_text = await ask_ara(text, u_id)
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": response_text}}],
                "quickReplies": get_quick_replies()
            }
        }
    except Exception as e:
        print(f"🚨 Main Error: {e}")
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "파도가 높네요. 다시 시도해주세요! 🌊"}}]}}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)