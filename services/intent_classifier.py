from typing import Literal
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
import os
from dotenv import load_dotenv

load_dotenv()


class IntentClassifier:
    """사용자 의도를 분류하는 서비스"""
    
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=os.getenv("OPENAI_API_KEY")
        )
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """당신은 한국해양대학교(KMOU) 챗봇의 의도 분류기입니다.
사용자의 질문을 분석하여 다음 중 하나로 분류하세요:
- "shuttle": 셔틀 버스 관련 질문 (시간표, 위치, 실시간 정보 등)
- "general": 일반적인 질문 (학교 정보, 학사, 생활 정보 등)

응답은 반드시 "shuttle" 또는 "general" 중 하나만 반환하세요."""),
            ("user", "{user_message}")
        ])
    
    async def classify(self, message: str) -> Literal["shuttle", "general"]:
        """사용자 메시지의 의도를 분류"""
        chain = self.prompt | self.llm
        response = await chain.ainvoke({"user_message": message})
        
        intent = response.content.strip().lower()
        if "shuttle" in intent:
            return "shuttle"
        return "general"
