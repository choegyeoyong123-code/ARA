import sys
import os

# ==========================================
# [Render 배포용] SQLite 버전 패치 (ChromaDB 호환)
# ==========================================
try:
    # type: ignore 코멘트를 추가하여 Pylance/Pyright 경고 무시
    __import__('pysqlite3')
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv()

# ==========================================
# 메인 로직
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
    # [핵심 수정] 데이터 폴더가 없으면 생성 (FileNotFoundError 방지)
    # -----------------------------------------------------
    if not data_dir.exists():
        print(f"⚠️ [Warning] '{data_dir}' 폴더가 없습니다. 빈 폴더를 생성합니다.")
        data_dir.mkdir(parents=True, exist_ok=True)
        # 폴더를 막 만들었으니 안에 파일이 없겠죠? 바로 종료합니다.
        print("➡️ 데이터가 없어 DB 생성을 건너뛰고 서버를 시작합니다.")
        return

    # 2. 데이터 로드 (안전하게 시도)
    try:
        loader = DirectoryLoader(str(data_dir), glob="**/*.txt", loader_cls=TextLoader, loader_kwargs={'encoding': 'utf-8'})
        documents = loader.load()
    except Exception as e:
        print(f"⚠️ [Warning] 데이터 로딩 중 오류 발생 (무시함): {e}")
        documents = []

    # 3. 문서 유무 확인
    if not documents:
        print("⚠️ [Info] 학습할 문서(.txt)가 없습니다. DB 갱신을 건너뜁니다.")
        return

    # 4. 임베딩 및 저장 (API 키 확인 포함)
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ [Error] OPENAI_API_KEY가 없습니다. DB 생성을 건너뜁니다.")
        return

    print(f"📚 {len(documents)}개의 문서를 발견했습니다. 임베딩 시작...")
    
    try:
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=80)
        texts = text_splitter.split_documents(documents)

        embeddings = OpenAIEmbeddings()
        vector_db = Chroma.from_documents(
            documents=texts, 
            embedding=embeddings, 
            persist_directory=str(db_dir)
        )
        print(f"✅ [Success] 학습 완료! {len(texts)}개의 지식 조각이 저장되었습니다.")
    except Exception as e:
        print(f"❌ [Error] ChromaDB 생성 실패: {e}")

if __name__ == "__main__":
    main()