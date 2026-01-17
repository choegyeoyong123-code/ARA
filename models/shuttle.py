from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class ShuttleInfo(BaseModel):
    route: str
    current_location: Optional[str] = None
    next_stop: Optional[str] = None
    estimated_arrival: Optional[datetime] = None
    status: str  # "운행중", "대기중", "운행종료" 등


class ShuttleSchedule(BaseModel):
    route: str
    departure_times: List[str]
    last_departure: Optional[str] = None
