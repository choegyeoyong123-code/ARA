# =========================
# SQLite 버전 패치 (Render 배포 호환성)
# =========================
# [중요] Render 등 리눅스 환경에서 구버전 SQLite 문제 해결을 위한 패치
# pysqlite3를 시도하고, 성공하면 시스템의 sqlite3 모듈을 pysqlite3로 교체
# 이 코드는 load_dotenv()보다 반드시 먼저 실행되어야 함
import os
import sys
import sys  # <--- [핵심] 이 줄이 없으면 에러가 납니다!
try:
    __import__('pysqlite3')
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    # pysqlite3-binary가 설치되지 않은 로컬 환경(윈도우 등)을 위한 예외 처리
    pass
# =========================
# 환경 변수 로드
# =========================
from dotenv import load_dotenv

# .env 파일에 저장된 키를 불러옵니다.
load_dotenv()

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
