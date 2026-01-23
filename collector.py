import requests
import os
from pathlib import Path

# 1. 현재 파일의 절대 경로를 가져와서 'university_data' 폴더 경로 확정
# OneDrive 환경에서도 가장 안전한 pathlib을 사용합니다.
current_dir = Path(__file__).parent.absolute()
data_dir = current_dir / "university_data"

# 2. 폴더 강제 생성 및 권한 확인
try:
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"✅ 데이터 저장 경로 확보: {data_dir}")
except Exception as e:
    print(f"⚠️ 폴더 생성 중 경고 (무시 가능): {e}")

def collect_university_info(target_url, filename):
    jina_url = f"https://r.jina.ai/{target_url}"
    print(f"📡 데이터 수집 중: [{filename}] ...")
    
    try:
        # Jina AI 서버에 요청
        response = requests.get(jina_url, timeout=30)
        response.raise_for_status()
        
        # 3. 파일 저장 (pathlib 객체를 사용하여 경로 결합)
        file_path = data_dir / f"{filename}.txt"
        
        # 파일을 쓸 때, 기존에 혹시 모를 잠금을 피하기 위해 명시적으로 인코딩 설정
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(response.text)
            
        print(f"  ✨ 성공적으로 저장됨: {file_path.name}")
        
    except Exception as e:
        print(f"  ❌ 수집 실패 ({filename}): {e}")

# --- KMOU 게시판 목록 ---
# --- 수집할 게시판 목록에 '식단 정보' 추가 ---
urls_to_crawl = {
    "notice_general": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=2032&bbsId=10373",
    "academic_guide": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=2033&bbsId=11786",
    "scholarship_guide": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=5691&bbsId=10004365",
    "events_seminar": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=2034&bbsId=10375",
    "cafeteria_menu": "https://www.kmou.ac.kr/coop/dv/dietView/selectDietDateView.do?mi=1189" # 추가된 식단 URL
}
for name, url in urls_to_crawl.items():
    collect_university_info(url, name)

print("\n🚀 모든 수집 작업이 끝났습니다. 이제 ingest.py를 실행해 보세요!")