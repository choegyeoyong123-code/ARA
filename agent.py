import os
import json
from openai import OpenAI
# tools.py 함수들 임포트
from tools import (
    TOOLS_SPEC, 
    get_weather, 
    get_bus_190, 
    get_meal, 
    search_places, 
    get_academic_calendar, 
    get_shuttle_info,
    get_school_link 
)

# [중요] API Key는 반드시 환경변수에서 가져옵니다 (보안 필수)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def ask_ara(user_input, user_id="test_user"):
    messages = [
        {"role": "system", "content": """너는 한국해양대학교 AI 짝꿍 '아라'야.
        친구처럼 다정하게 존댓말을 써. 이모지를 적절히 사용해. 🐬💙
        답변은 카카오톡 환경을 고려해 3줄 이내로 짧고 명확하게 해줘."""},
        {"role": "user", "content": user_input}
    ]

    try:
        # 1. GPT에게 질문 (가장 빠른 gpt-4o-mini 사용)
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # <--- 속도 해결의 핵심!
            messages=messages,
            tools=TOOLS_SPEC,
            tool_choice="auto"
        )
        
        response_message = response.choices[0].message
        
        # 2. 도구(함수) 사용 여부 확인
        if response_message.tool_calls:
            messages.append(response_message)
            
            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                # 도구 실행
                tool_result = "정보 없음"
                if function_name == "get_weather": tool_result = get_weather()
                elif function_name == "get_bus_190": tool_result = get_bus_190()
                elif function_name == "get_meal": tool_result = get_meal()
                elif function_name == "get_academic_calendar": tool_result = get_academic_calendar()
                elif function_name == "get_shuttle_info": tool_result = get_shuttle_info()
                elif function_name == "get_school_link": tool_result = get_school_link(function_args.get("category"))
                elif function_name == "search_places": tool_result = search_places(function_args.get("query"))

                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": str(tool_result)
                })

            # 3. 최종 답변 생성
            final_response = client.chat.completions.create(
                model="gpt-4o-mini", # <--- 여기도 mini 사용
                messages=messages
            )
            return final_response.choices[0].message.content
        
        return response_message.content

    except Exception as e:
        print(f"Error: {e}")
        return "지금 잠시 연결이 불안정해! 😵‍💫 3초 뒤에 다시 말 걸어줘."