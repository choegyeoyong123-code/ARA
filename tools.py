# [tools.py 내부 get_bus_arrival 함수 수정]

async def get_bus_arrival(bus_number: str = None):
    try:
        async with httpx.AsyncClient(verify=False) as client:
            # 1. 검색어: '해양대' (넓게 검색)
            search_url = "https://api.odsay.com/v1/api/searchStation"
            search_params = {
                "apiKey": ODSAY_KEY,
                "stationName": "해양대", 
                "CID": "6", # 부산
            }
            
            search_res = await client.get(search_url, params=search_params, timeout=10.0)
            search_data = search_res.json()
            
            stations = search_data.get('result', {}).get('station', [])
            
            if not stations:
                print("[DEBUG] '해양대' 검색 결과 없음")
                return json.dumps({"status": "empty", "msg": "정류장을 찾을 수 없습니다."}, ensure_ascii=False)

            # 2. 우선순위 필터링 (여기에 '해양대구본관' 추가!)
            target_station_id = None
            
            # 우리가 찾는 정류장 이름 후보들 (앞에 있을수록 우선순위 높음)
            priority_names = ["해양대구본관", "한국해양대학교", "한국해양대", "해양대종점"]

            for st in stations:
                name = st['stationName']
                sid = st['stationID']
                print(f"[DEBUG] 검색된 후보: {name} (ID: {sid})") # 로그 확인용
                
                if name in priority_names:
                    target_station_id = sid
                    print(f"[DEBUG] 우선순위 매칭 성공: {name}")
                    break
            
            # 매칭된 게 없으면 목록의 맨 처음 것 선택
            if not target_station_id:
                target_station_id = stations[0]['stationID']
                print(f"[DEBUG] 매칭 실패. 첫 번째 결과({stations[0]['stationName']}) 자동 선택")

            # 3. 실시간 정보 조회 (이하 동일)
            realtime_url = "https://api.odsay.com/v1/api/realtimeStation"
            realtime_params = {
                "apiKey": ODSAY_KEY,
                "stationID": target_station_id
            }

            arrival_res = await client.get(realtime_url, params=realtime_params, timeout=10.0)
            arrival_data = arrival_res.json()

            if not arrival_data.get('result', {}).get('realtimeArrivalList'):
                return json.dumps({"status": "empty", "msg": "도착 정보 없음 (차고지 대기 중)"}, ensure_ascii=False)

            arrival_list = arrival_data['result']['realtimeArrivalList']
            bus_results = []
            
            for bus in arrival_list:
                route_name = bus.get('routeNm')
                arrival_msg = bus.get('arrival1', {}).get('msg1', '정보없음')
                
                if bus_number and bus_number not in route_name: continue

                bus_results.append({
                    "bus_no": route_name,
                    "status": arrival_msg,
                    "low_plate": "저상" if bus.get('lowPlate1') == '1' else "일반"
                })

            if not bus_results:
                 return json.dumps({"status": "empty", "msg": f"{bus_number}번 정보 없음"}, ensure_ascii=False)

            return json.dumps({"status": "success", "buses": bus_results}, ensure_ascii=False)

    except Exception as e:
        print(f"[ERROR] Bus Logic: {e}")
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)