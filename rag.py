"""
RAG (Retrieval-Augmented Generation) 모듈
- 한국해양대학교 학칙 및 규정 검색을 위한 Vector DB (FAISS)
- 사용자 질문과 유사한 학칙 텍스트를 검색하여 LLM 컨텍스트로 제공
"""

import os
import json
import pickle
from pathlib import Path
from typing import List, Optional, Tuple
import numpy as np
import faiss
from openai import AsyncOpenAI

# OpenAI 임베딩 모델 (텍스트 → 벡터 변환)
_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIM = 1536  # text-embedding-3-small 차원

# Vector DB 저장 경로
_VECTOR_DB_DIR = Path(__file__).resolve().parent
_VECTOR_DB_INDEX_PATH = _VECTOR_DB_DIR / "kmou_regulations.index"
_VECTOR_DB_METADATA_PATH = _VECTOR_DB_DIR / "kmou_regulations_metadata.pkl"

# 전역 변수: FAISS 인덱스 및 메타데이터
_index: Optional[faiss.Index] = None
_metadata: List[dict] = []
_openai_client: Optional[AsyncOpenAI] = None


def _get_openai_client() -> AsyncOpenAI:
    """OpenAI 클라이언트 싱글톤"""
    global _openai_client
    if _openai_client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
        _openai_client = AsyncOpenAI(api_key=api_key)
    return _openai_client


async def _get_embedding(text: str) -> List[float]:
    """
    텍스트를 벡터로 변환 (OpenAI Embedding API)
    """
    client = _get_openai_client()
    try:
        response = await client.embeddings.create(
            model=_EMBEDDING_MODEL,
            input=[text],
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"[RAG Error] Embedding 생성 실패: {e}")
        return [0.0] * _EMBEDDING_DIM


def _load_vector_db() -> Tuple[Optional[faiss.Index], List[dict]]:
    """
    저장된 FAISS 인덱스와 메타데이터 로드
    """
    try:
        if not _VECTOR_DB_INDEX_PATH.exists() or not _VECTOR_DB_METADATA_PATH.exists():
            return None, []
        
        index = faiss.read_index(str(_VECTOR_DB_INDEX_PATH))
        with open(_VECTOR_DB_METADATA_PATH, "rb") as f:
            metadata = pickle.load(f)
        
        return index, metadata
    except Exception as e:
        print(f"[RAG Warning] Vector DB 로드 실패: {e}")
        return None, []


def _save_vector_db(index: faiss.Index, metadata: List[dict]) -> None:
    """
    FAISS 인덱스와 메타데이터 저장
    """
    try:
        faiss.write_index(index, str(_VECTOR_DB_INDEX_PATH))
        with open(_VECTOR_DB_METADATA_PATH, "wb") as f:
            pickle.dump(metadata, f)
    except Exception as e:
        print(f"[RAG Error] Vector DB 저장 실패: {e}")


def initialize_vector_db(regulations: List[dict]) -> bool:
    """
    학칙 데이터로 Vector DB 초기화
    - regulations: [{"text": "학칙 텍스트", "source": "출처", "title": "제목"}, ...]
    """
    global _index, _metadata
    
    if not regulations:
        print("[RAG Warning] 초기화할 학칙 데이터가 없습니다.")
        return False
    
    try:
        # 기존 인덱스 로드 시도
        existing_index, existing_metadata = _load_vector_db()
        if existing_index is not None and len(existing_metadata) > 0:
            _index = existing_index
            _metadata = existing_metadata
            print(f"[RAG] 기존 Vector DB 로드 완료 ({len(_metadata)}개 문서)")
            return True
        
        # 새 인덱스 생성
        index = faiss.IndexFlatL2(_EMBEDDING_DIM)
        metadata = []
        
        print(f"[RAG] Vector DB 초기화 중... ({len(regulations)}개 문서)")
        # 실제 구현에서는 regulations의 각 항목을 임베딩하여 인덱스에 추가
        # 여기서는 비동기 임베딩이 필요하므로, 실제 사용 시 get_university_context에서
        # 동적으로 검색하거나, 별도의 초기화 스크립트에서 실행해야 함
        
        _index = index
        _metadata = metadata
        print("[RAG] Vector DB 초기화 완료 (빈 인덱스 - 실제 데이터는 별도 스크립트로 추가 필요)")
        return True
    except Exception as e:
        print(f"[RAG Error] Vector DB 초기화 실패: {e}")
        return False


async def get_university_context(query: str, top_k: int = 5) -> Optional[str]:
    """
    사용자 질문과 유사한 한국해양대학교 학칙 및 규정 텍스트를 검색
    
    Args:
        query: 사용자 질문
        top_k: 반환할 최상위 문서 개수 (기본 3개)
    
    Returns:
        검색된 학칙 텍스트를 포맷팅한 문자열 (없으면 None)
    """
    global _index, _metadata
    
    # 인덱스가 없거나 비어있으면 None 반환
    if _index is None or len(_metadata) == 0:
        # 기존 인덱스 로드 시도
        _index, _metadata = _load_vector_db()
        if _index is None or len(_metadata) == 0:
            return None
    
    try:
        # 쿼리 임베딩 생성
        query_embedding = await _get_embedding(query)
        query_vector = np.array([query_embedding], dtype=np.float32)
        
        # FAISS 검색 (검색 범위 확대: top_k * 2로 검색 후 필터링)
        search_k = min(top_k * 2, _index.ntotal)
        if search_k == 0:
            return None
        
        distances, indices = _index.search(query_vector, search_k)
        
        # 검색 결과 포맷팅 및 필터링
        results = []
        seen_titles = set()  # 중복 제거
        
        # 키워드 기반 가중치 적용 (휴학, 학사 등 중요 키워드)
        important_keywords = ["휴학", "학사", "장학금", "졸업", "수강신청", "복학", "등록금"]
        query_lower = query.lower()
        has_important_keyword = any(kw in query_lower for kw in important_keywords)
        
        for i, idx in enumerate(indices[0]):
            if idx < len(_metadata):
                doc = _metadata[idx]
                text = doc.get("text", "")
                title = doc.get("title", "학칙")
                source = doc.get("source", "")
                distance = float(distances[0][i])
                
                # 중요 키워드가 있으면 임계값 완화 (1.0 -> 1.5)
                threshold = 1.5 if has_important_keyword else 1.0
                if distance > threshold:
                    continue
                
                # 중복 제거
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                
                results.append(f"[{title}]\n{text}")
                if source:
                    results[-1] += f"\n(출처: {source})"
                
                # top_k만큼만 반환
                if len(results) >= top_k:
                    break
        
        if not results:
            return None
        
        return "\n\n---\n\n".join(results)
    
    except Exception as e:
        print(f"[RAG Error] 검색 실패: {e}")
        return None


# 초기화: 기존 Vector DB 로드 시도
try:
    _index, _metadata = _load_vector_db()
    if _index is not None:
        print(f"[RAG] Vector DB 로드 완료 ({len(_metadata)}개 문서)")
except Exception:
    pass
