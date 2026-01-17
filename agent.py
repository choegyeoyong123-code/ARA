# ... (기존 임포트 생략)import os
from openai import AsyncOpenAI
from database import get_history, save_history  # 👈 이 줄이 반드시 있어야 합니다!
from tools import TOOLS_SPEC # ... 기타 도구들
async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    from tools import (
    TOOLS_SPEC, get_weather_real, get_festivals, 
    get_busan_restaurants, get_hospitals, get_meal, 
    get_inside_bus_status, get_shuttle_info,
    get_user_profile  # 👈 이 한 줄이 누락되어 에러가 발생했습니다.
) # [Admin 기능 활용] 사용자의 이름을 가져와 첫 인사를 구성합니다.
    user_name = await get_user_profile(user_id)
    
    if not history:
        history.append({
            "role": "system", 
            "content": f"너는 한국해양대 AI 아라야. 사용자의 이름은 {user_name}이야. [지침] 1. 도구 결과에만 근거할 것. 2. 환각 금지. 3. 3줄 이내 답변."
        })
    
    # ... (기존 비동기 도구 호출 로직 동일)