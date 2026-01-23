import sys
import os

# ==========================================
# [Render 배포용] SQLite 버전 패치 (ChromaDB 호환)
# ==========================================
try:
    __import__('pysqlite3')
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv()

# ==========================================
# 메인 로직 import
# ==========================================
from pathlib import Path
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

def main():
    # 1. 경로 설정 (절대 경로)
    current_dir = Path(__file__).parent.absolute()
    data_dir = current_dir / "university_data"
    db_dir = current_dir / "university_db"

    print(f"🔍 [Ingest] 데이터 경로 확인: {data_dir}")

    # -----------------------------------------------------
    # [장애 방지 1] 데이터 폴더가 없으면 생성 (Crash 방지)
    # -----------------------------------------------------
    if not data_dir.exists():
        print(f"⚠️ [Warning] '{data_dir}' 폴더가 없습니다. 빈 폴더를 생성합니다.")
        data_dir.mkdir(parents=True, exist_ok=True)
    
    # -----------------------------------------------------
    # [장애 방지 2] API 키 누락 시 안전 종료
    # -----------------------------------------------------
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ [Error] OPENAI_API_KEY가 환경 변수에 없습니다. DB 생성을 건너뜁니다.")
        return  # 에러 없이 함수 종료 -> 서버 실행 단계로 넘어감

    # 2. 데이터 로드 (예외 처리 포함)
    try:
        loader = DirectoryLoader(str(data_dir), glob="**/*.txt", loader_cls=TextLoader, loader_kwargs={'encoding': 'utf-8'})
        documents = loader.load()
    except Exception as e:
        print(f"⚠️ [Warning] 데이터 로딩 중 오류 발생: {e}")
        documents = []

    # 3. 문서 유무 확인
    if not documents:
        print("⚠️ [Info] 학습할 문서(.txt)가 없습니다. DB 갱신을 건너뜁니다.")
        print("   (collector.py가 실행되지 않았거나 데이터 수집에 실패했을 수 있습니다.)")
        return  # 정상 종료

    # 4. 텍스트 분할 및 저장
    print(f"📚 {len(documents)}개의 문서를 발견했습니다. 임베딩을 시작합니다...")
    
    try:
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=80)
        texts = text_splitter.split_documents(documents)

        embeddings = OpenAIEmbeddings()
        
        # 기존 DB가 있으면 로드, 없으면 생성
        vector_db = Chroma.from_documents(
            documents=texts, 
            embedding=embeddings, 
            persist_directory=str(db_dir)
        )
        print(f"✅ [Success] 학습 완료! {len(texts)}개의 지식 조각이 '{db_dir.name}'에 저장되었습니다.")
        
    except Exception as e:
        print(f"❌ [Error] 벡터 DB 생성 중 오류 발생: {e}")
        # 여기서도 에러를 뱉지 않고 로그만 남김

if __name__ == "__main__":
    main()