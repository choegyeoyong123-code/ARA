from typing import List, Optional
from models.shuttle import ShuttleInfo, ShuttleSchedule
from datetime import datetime, timedelta
import httpx
import os
from dotenv import load_dotenv

load_dotenv()


class ShuttleService:
    """셔틀 버스 실시간 정보 서비스"""
    
    def __init__(self):
        self.api_url = os.getenv("SHUTTLE_API_URL", "")
        self.api_key = os.getenv("SHUTTLE_API_KEY", "")
        # 예시 데이터 (실제 API가 없을 경우 사용)
        self.mock_schedules = {
            "본관-기숙사": [
                "08:00", "08:30", "09:00", "09:30", "10:00",
                "10:30", "11:00", "11:30", "12:00", "12:30",
                "13:00", "13:30", "14:00", "14:30", "15:00",
                "15:30", "16:00", "16:30", "17:00", "17:30",
                "18:00", "18:30", "19:00", "19:30", "20:00"
            ],
            "기숙사-본관": [
                "08:15", "08:45", "09:15", "09:45", "10:15",
                "10:45", "11:15", "11:45", "12:15", "12:45",
                "13:15", "13:45", "14:15", "14:45", "15:15",
                "15:45", "16:15", "16:45", "17:15", "17:45",
                "18:15", "18:45", "19:15", "19:45", "20:15"
            ]
        }
    
    async def get_realtime_info(self, route: Optional[str] = None) -> List[ShuttleInfo]:
        """실시간 셔틀 정보 조회"""
        if self.api_url:
            try:
                async with httpx.AsyncClient() as client:
                    headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                    response = await client.get(
                        f"{self.api_url}/realtime",
                        headers=headers,
                        params={"route": route} if route else {},
                        timeout=5.0
                    )
                    if response.status_code == 200:
                        data = response.json()
                        return [ShuttleInfo(**item) for item in data]
            except Exception as e:
                print(f"셔틀 API 호출 실패: {e}")
        
        # Mock 데이터 반환
        return [
            ShuttleInfo(
                route="본관-기숙사",
                current_location="본관",
                next_stop="기숙사",
                estimated_arrival=datetime.now() + timedelta(minutes=5),
                status="운행중"
            ),
            ShuttleInfo(
                route="기숙사-본관",
                current_location="기숙사",
                next_stop="본관",
                estimated_arrival=datetime.now() + timedelta(minutes=8),
                status="운행중"
            )
        ]
    
    async def get_schedule(self, route: Optional[str] = None) -> List[ShuttleSchedule]:
        """셔틀 시간표 조회"""
        schedules = []
        for route_name, times in self.mock_schedules.items():
            if route and route not in route_name:
                continue
            schedules.append(ShuttleSchedule(
                route=route_name,
                departure_times=times,
                last_departure=times[-1] if times else None
            ))
        return schedules
    
    async def get_next_departure(self, route: str) -> Optional[str]:
        """다음 출발 시간 조회"""
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        
        times = self.mock_schedules.get(route, [])
        for time_str in times:
            if time_str > current_time:
                return time_str
        return times[0] if times else None  # 다음날 첫 차
