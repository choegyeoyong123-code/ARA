import os
import httpx
import asyncio
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv() 

async def test_apis():
    print("--- 🔍 ARA API 진단 시작 ---")
    
    # 1. API 키 확인
    kakao_key = os.getenv("KAKAO_REST_API_KEY")
    public_key = os.getenv("DATA_GO_KR_SERVICE_KEY")
    
    print(f"1. KAKAO Key: {'✅ 보유' if kakao_key else '❌ 없음'}")
    print(f"2. PUBLIC Key: {'✅ 보유' if public_key else '❌ 없음'}")

    # 3. 190번 버스 테스트 (부산BIMS)
    if public_key:
        print("\n[3. 190번 버스 API 테스트]")
        url = "http://apis.data.go.kr/6260000/BusanBIMS/bitArrByArsno"
        # 03058: 해양대 본관(IN)
        params = {"serviceKey": public_key, "arsno": "03058"} 
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, params=params, timeout=5.0)
                print(f"   👉 상태 코드: {res.status_code}")
                
                if res.status_code == 200:
                    if "ServiceKey is not registered" in res.text:
                        print("   👉 [에러] 서비스 키가 등록되지 않았거나 승인 대기 중입니다.")
                    elif "190" in res.text:
                        print("   👉 [성공] 190번 데이터 수신 성공!")
                    else:
                        print(f"   👉 [주의] 응답은 왔으나 190번 없음 (내용 일부): {res.text[:100]}")
                else:
                    print(f"   👉 [실패] HTTP 에러: {res.status_code}")
        except Exception as e:
             print(f"   👉 [실패] 연결 오류: {e}")
    else:
        print("\n[3. 190번 버스] 키가 없어 건너뜁니다.")

    # 4. 약국/병원 테스트 (카카오 로컬)
    if kakao_key:
        print("\n[4. 카카오 로컬 API 테스트]")
        url = "https://dapi.kakao.com/v2/local/search/keyword.json"
        headers = {"Authorization": f"KakaoAK {kakao_key}"}
        # 해양대 좌표 중심 5km 반경 약국 검색
        params = {
            "query": "약국", 
            "x": "129.086944", 
            "y": "35.074441", 
            "radius": 5000
        } 
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=headers, params=params)
                print(f"   👉 상태 코드: {res.status_code}")
                
                if res.status_code == 200:
                    data = res.json()
                    count = len(data.get('documents', []))
                    print(f"   👉 [성공] 검색된 장소: {count}개")
                else:
                    print(f"   👉 [실패] 에러 코드: {res.status_code}")
        except Exception as e:
            print(f"   👉 [실패] 카카오 연결 오류: {e}")
    else:
        print("\n[4. 카카오 로컬] 키가 없어 건너뜁니다.")

if __name__ == "__main__":
    asyncio.run(test_apis())