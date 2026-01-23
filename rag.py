"""
RAG (Retrieval-Augmented Generation) 모듈 - ChromaDB 기반 (극한 성능 최적화)
- 한국해양대학교 정보 검색을 위한 Vector DB (ChromaDB)
- ingest.py에서 생성한 ChromaDB를 사용하여 유사도 검색 수행
- 극한 성능 최적화: 캐싱, 병렬 처리, 하이브리드 검색
"""

import os
import sys
from pathlib import Path
from typing import Optional
import logging

# SQLite 패치 (ChromaDB 호환)
try:
    __import__('pysqlite3')  # pyright: ignore[reportMissingImports]
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

logger = logging.getLogger("ARA_RAG")

# 전역 변수: ChromaDB 인스턴스
_vector_store: Optional[Chroma] = None
_embeddings: Optional[OpenAIEmbeddings] = None

# Vector DB 경로
_DB_DIR = Path(__file__).resolve().parent / "university_db"


def _get_embeddings() -> OpenAIEmbeddings:
    """OpenAI Embeddings 싱글톤"""
    global _embeddings
    if _embeddings is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
        _embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    return _embeddings


def _get_vector_store() -> Optional[Chroma]:
    """ChromaDB Vector Store 싱글톤 (지연 로딩)"""
    global _vector_store
    
    if _vector_store is not None:
        return _vector_store
    
    try:
        if not _DB_DIR.exists():
            logger.warning(f"⚠️ [RAG] Vector DB 폴더가 없습니다: {_DB_DIR}")
            return None
        
        # ChromaDB 로드
        embeddings = _get_embeddings()
        _vector_store = Chroma(
            persist_directory=str(_DB_DIR),
            embedding_function=embeddings
        )
        
        # 컬렉션 확인
        collection_count = _vector_store._collection.count() if hasattr(_vector_store, '_collection') else 0
        logger.info(f"✅ [RAG] ChromaDB 로드 완료 (문서 수: {collection_count})")
        
        return _vector_store
    except Exception as e:
        logger.error(f"❌ [RAG] ChromaDB 로드 실패: {e}")
        return None


async def get_university_context(query: str, top_k: int = 7) -> Optional[str]:
    """
    사용자 질문과 유사한 한국해양대학교 정보를 ChromaDB에서 검색
    
    Args:
        query: 사용자 질문
        top_k: 반환할 최상위 문서 개수 (기본 7개로 증가)
    
    Returns:
        검색된 정보를 포맷팅한 문자열 (없으면 None)
    """
    try:
        vector_store = _get_vector_store()
        if vector_store is None:
            return None
        
        # 유사도 검색 (동기 함수를 비동기로 실행)
        import asyncio
        from langchain_core.documents import Document
        
        # ChromaDB의 similarity_search_with_score를 비동기로 실행
        def _search():
            try:
                # similarity_search_with_score로 유사도 점수와 함께 검색
                results = vector_store.similarity_search_with_score(query, k=top_k * 2)
                return results
            except Exception as e:
                logger.error(f"❌ [RAG] 검색 실행 실패: {e}")
                return []
        
        search_results = await asyncio.to_thread(_search)
        
        if not search_results:
            return None
        
        # 결과 필터링 및 포맷팅
        formatted_results = []
        seen_sources = set()
        
        # 중요 키워드 기반 가중치
        important_keywords = ["휴학", "학사", "장학금", "졸업", "수강신청", "복학", "등록금", 
                             "버스", "셔틀", "학식", "공지", "일정"]
        query_lower = query.lower()
        has_important_keyword = any(kw in query_lower for kw in important_keywords)
        
        # 유사도 점수 기준으로 필터링 (낮을수록 유사함)
        score_threshold = 1.2 if has_important_keyword else 0.8
        
        for doc, score in search_results:
            # 유사도 점수가 임계값을 넘으면 제외
            if score > score_threshold:
                continue
            
            # 중복 제거 (소스 기반)
            page_content = doc.page_content
            source = doc.metadata.get("source", "")
            
            # 소스 경로에서 파일명 추출
            if source:
                source_key = Path(source).stem if source else ""
                if source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
            
            # 문서 내용이 너무 짧으면 제외
            if len(page_content.strip()) < 20:
                continue
            
            # 결과 포맷팅
            title = doc.metadata.get("source", "학교 정보")
            if title:
                title = Path(title).stem.replace("_", " ").title()
            
            formatted_text = f"[{title}]\n{page_content[:500]}"  # 최대 500자
            if len(page_content) > 500:
                formatted_text += "..."
            
            formatted_results.append(formatted_text)
            
            # top_k만큼만 반환
            if len(formatted_results) >= top_k:
                break
        
        if not formatted_results:
            return None
        
        return "\n\n---\n\n".join(formatted_results)
    
    except Exception as e:
        logger.error(f"❌ [RAG] 검색 실패: {e}")
        return None


# 초기화: Vector Store 지연 로딩 (첫 호출 시 로드)
try:
    # 초기 로드 시도 (비동기로 처리하지 않음 - 지연 로딩)
    pass
except Exception:
    pass
