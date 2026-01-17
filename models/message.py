from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(BaseModel):
    role: MessageRole
    content: str
    timestamp: Optional[datetime] = None

    class Config:
        use_enum_values = True


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    intent: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
