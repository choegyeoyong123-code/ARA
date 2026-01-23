from langchain_community.document_loaders import RecursiveUrlLoader
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from bs4 import BeautifulSoup
import re

# 1. 크롤링할 학교 주소 설정
url = "https://www.kmou.ac.kr/kmou/main.do" # 예시: 한국해양대 메인

# 2. 웹 데이터 로더 설정 (깊이 2단계까지 탐색)
loader = RecursiveUrlLoader(
    url=url, 
    max_depth=2, 
    extractor=lambda x: BeautifulSoup(x, "lxml").text # HTML에서 텍스트만 추출
)
docs = loader.load()

# 3. 텍스트 정제 (불필요한 공백 제거)
for doc in docs:
    doc.page_content = re.sub(r'\n+', '\n', doc.page_content)

# 4. 텍스트 분할 및 저장
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
splits = text_splitter.split_documents(docs)

vector_db = Chroma.from_documents(
    documents=splits, 
    embedding=OpenAIEmbeddings(), 
    persist_directory="./university_db"
)

print(f"✅ {len(docs)}개의 웹 페이지로부터 데이터를 수집하여 인덱싱 완료!")