import os
from openai import OpenAI

# 1단계에서 등록한 환경 변수를 가져옵니다.
api_key = os.environ.get("OPENAI_API_KEY")

if not api_key:
    print("❌ 환경 변수가 설정되지 않았습니다. 1단계를 다시 확인해주세요.")
else:
    try:
        # 클라이언트 연결 시도
        client = OpenAI(api_key=api_key)
        print(f"✅ API Key 인식 성공! (키 시작: {api_key[:10]}...)")
        
        # 실제 연결 테스트 (간단한 요청)
        client.models.list()
        print("🎉 OpenAI 서버 연결까지 성공했습니다!")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")