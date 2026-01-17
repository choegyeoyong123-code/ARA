from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.memory import ConversationBufferMemory
from typing import Dict, Optional
import os
from dotenv import load_dotenv

load_dotenv()


class ChatService:
    """일반 챗봇 서비스"""
    
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            api_key=os.getenv("OPENAI_API_KEY")
        )
        self.memories: Dict[str, ConversationBufferMemory] = {}
        self.system_prompt = """당신은 한국해양대학교(KMOU)의 친절한 AI 어시스턴트입니다.
학생들에게 학교 관련 정보를 제공하고 질문에 답변합니다.

주요 정보:
- 한국해양대학교는 부산광역시 영도구에 위치한 국립대학교입니다.
- 해양, 항해, 기계공학, 전기전자공학 등 다양한 학과가 있습니다.
- 학교 생활, 학사 일정, 시설 안내 등에 대해 도움을 드립니다.

친절하고 정확한 정보를 제공하되, 모르는 내용은 솔직하게 말씀드리세요."""
    
    def _get_memory(self, conversation_id: str) -> ConversationBufferMemory:
        """대화 기록 메모리 가져오기"""
        if conversation_id not in self.memories:
            self.memories[conversation_id] = ConversationBufferMemory(
                return_messages=True
            )
        return self.memories[conversation_id]
    
    async def get_response(
        self,
        message: str,
        conversation_id: str = "default",
        context: Optional[Dict] = None
    ) -> str:
        """일반 질문에 대한 응답 생성"""
        memory = self._get_memory(conversation_id)
        
        # 대화 기록 가져오기
        history = memory.chat_memory.messages
        
        # 프롬프트 생성
        messages = [("system", self.system_prompt)]
        
        # 대화 기록 추가
        for msg in history:
            if msg.type == "human":
                messages.append(("user", msg.content))
            elif msg.type == "ai":
                messages.append(("assistant", msg.content))
        
        # 현재 메시지 추가
        messages.append(("user", message))
        
        prompt = ChatPromptTemplate.from_messages(messages)
        chain = prompt | self.llm
        
        response = await chain.ainvoke({})
        response_text = response.content
        
        # 메모리에 저장
        memory.chat_memory.add_user_message(message)
        memory.chat_memory.add_ai_message(response_text)
        
        return response_text
