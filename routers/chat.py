from fastapi import APIRouter, HTTPException
from models.message import ChatRequest, ChatResponse
from graph.chat_graph import create_chat_graph
import uuid

router = APIRouter(prefix="/chat", tags=["chat"])

# 그래프 인스턴스 생성
chat_graph = create_chat_graph()


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """챗봇과 대화"""
    try:
        conversation_id = request.conversation_id or str(uuid.uuid4())
        
        result = await chat_graph.process(
            message=request.message,
            conversation_id=conversation_id
        )
        
        return ChatResponse(
            response=result["response"],
            conversation_id=conversation_id,
            intent=result.get("intent"),
            metadata=result.get("metadata", {})
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"챗봇 처리 중 오류 발생: {str(e)}")


@router.get("/health")
async def health_check():
    """헬스 체크"""
    return {"status": "healthy", "service": "KMOU Chatbot"}
