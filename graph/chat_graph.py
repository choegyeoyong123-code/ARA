from typing import TypedDict, Annotated, Literal
from langgraph.graph import StateGraph, END
from services.intent_classifier import IntentClassifier
from services.shuttle_service import ShuttleService
from services.chat_service import ChatService
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
import os
from dotenv import load_dotenv

load_dotenv()


class GraphState(TypedDict):
    """LangGraph 상태 정의"""
    message: str
    conversation_id: str
    intent: Literal["shuttle", "general", None]
    response: str
    metadata: dict


class ChatGraph:
    """LangGraph를 사용한 챗봇 그래프"""
    
    def __init__(self):
        self.intent_classifier = IntentClassifier()
        self.shuttle_service = ShuttleService()
        self.chat_service = ChatService()
        self.graph = self._create_graph()
    
    def _create_graph(self) -> StateGraph:
        """대화 흐름 그래프 생성"""
        workflow = StateGraph(GraphState)
        
        # 노드 추가
        workflow.add_node("classify_intent", self.classify_intent)
        workflow.add_node("handle_shuttle", self.handle_shuttle)
        workflow.add_node("handle_general", self.handle_general)
        
        # 엣지 추가
        workflow.set_entry_point("classify_intent")
        
        workflow.add_conditional_edges(
            "classify_intent",
            self.route_by_intent,
            {
                "shuttle": "handle_shuttle",
                "general": "handle_general"
            }
        )
        
        workflow.add_edge("handle_shuttle", END)
        workflow.add_edge("handle_general", END)
        
        return workflow.compile()
    
    async def classify_intent(self, state: GraphState) -> GraphState:
        """의도 분류"""
        intent = await self.intent_classifier.classify(state["message"])
        return {
            **state,
            "intent": intent
        }
    
    def route_by_intent(self, state: GraphState) -> Literal["shuttle", "general"]:
        """의도에 따라 라우팅"""
        return state.get("intent", "general")
    
    async def handle_shuttle(self, state: GraphState) -> GraphState:
        """셔틀 관련 질문 처리"""
        message = state["message"]
        
        # 셔틀 정보 조회
        realtime_info = await self.shuttle_service.get_realtime_info()
        schedules = await self.shuttle_service.get_schedule()
        
        # LLM을 사용하여 자연스러운 응답 생성
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            api_key=os.getenv("OPENAI_API_KEY")
        )
        
        info_text = "\n".join([
            f"- {info.route}: {info.status}, 다음 정류장: {info.next_stop}, 예상 도착: {info.estimated_arrival.strftime('%H:%M') if info.estimated_arrival else 'N/A'}"
            for info in realtime_info
        ])
        
        schedule_text = "\n".join([
            f"- {schedule.route}: {', '.join(schedule.departure_times[:5])}..."
            for schedule in schedules
        ])
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", """당신은 한국해양대학교 셔틀 버스 정보 안내원입니다.
다음 정보를 바탕으로 사용자의 질문에 친절하게 답변하세요.

실시간 정보:
{realtime_info}

시간표:
{schedule_info}"""),
            ("user", "{user_message}")
        ])
        
        chain = prompt | llm
        response = await chain.ainvoke({
            "realtime_info": info_text,
            "schedule_info": schedule_text,
            "user_message": message
        })
        
        return {
            **state,
            "response": response.content,
            "metadata": {
                "intent": "shuttle",
                "realtime_info": [info.dict() for info in realtime_info],
                "schedules": [schedule.dict() for schedule in schedules]
            }
        }
    
    async def handle_general(self, state: GraphState) -> GraphState:
        """일반 질문 처리"""
        response = await self.chat_service.get_response(
            message=state["message"],
            conversation_id=state.get("conversation_id", "default"),
            context=state.get("metadata", {})
        )
        
        return {
            **state,
            "response": response,
            "metadata": {
                "intent": "general"
            }
        }
    
    async def process(self, message: str, conversation_id: str = "default") -> dict:
        """메시지 처리"""
        initial_state: GraphState = {
            "message": message,
            "conversation_id": conversation_id,
            "intent": None,
            "response": "",
            "metadata": {}
        }
        
        result = await self.graph.ainvoke(initial_state)
        return result


def create_chat_graph() -> ChatGraph:
    """챗봇 그래프 인스턴스 생성"""
    return ChatGraph()
