import sys
import os

# SQLite 버전 패치 (Render 배포 호환성)
try:
    import pysqlite3
    sys.modules['sqlite3'] = pysqlite3
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv()

# 👇 이 아래부터 다른 import 작성

# =========================
# 나머지 import
# =========================
from pathlib import Path
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

# 1. 경로 설정 (절대 경로)
current_dir = Path(__file__).parent.absolute()
data_dir = current_dir / "university_data"
db_dir = current_dir / "university_db"

print(f"🔍 데이터 읽는 중: {data_dir}")

# 2. 데이터 로드
loader = DirectoryLoader(str(data_dir), glob="**/*.txt", loader_cls=TextLoader, loader_kwargs={'encoding': 'utf-8'})
documents = loader.load()

if not documents:
    print("❌ 학습할 서류가 없습니다. university_data 폴더를 확인하세요.")
else:
    # 3. 텍스트 분할 (청킹)
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=80)
    texts = text_splitter.split_documents(documents)

    # 4. 임베딩 및 벡터 DB 저장
    embeddings = OpenAIEmbeddings()
    vector_db = Chroma.from_documents(
        documents=texts, 
        embedding=embeddings, 
        persist_directory=str(db_dir)
    )
    print(f"✅ 학습 완료! {len(texts)}개의 지식 조각이 '{db_dir.name}'에 저장되었습니다.")
