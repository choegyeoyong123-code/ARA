import sys
import os

# SQLite 버전 패치 (Render 배포 호환성)
try:
    import pysqlite3  # type: ignore
    sys.modules['sqlite3'] = pysqlite3
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv()

# API 키 검증
if not os.getenv("OPENAI_API_KEY"):
    print("❌ OPENAI_API_KEY가 설정되지 않았습니다.")
    sys.exit(0)

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

# 폴더 안전장치: data_dir가 없으면 생성
if not data_dir.exists():
    data_dir.mkdir(parents=True, exist_ok=True)
    print("⚠️ 데이터 폴더가 없어 생성했습니다.")

print(f"🔍 데이터 읽는 중: {data_dir}")

# 2. 데이터 로드 (빈 데이터 처리)
try:
    loader = DirectoryLoader(str(data_dir), glob="**/*.txt", loader_cls=TextLoader, loader_kwargs={'encoding': 'utf-8'})
    documents = loader.load()
except Exception as e:
    print(f"⚠️ 데이터 로드 중 오류 발생: {e}")
    documents = []

if not documents:
    print("⚠️ 학습할 데이터가 없습니다. DB 생성을 건너뜁니다.")
    sys.exit(0)
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
