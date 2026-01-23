import cloudscraper
import os
from pathlib import Path
from bs4 import BeautifulSoup
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

# 3. CloudScraper 초기화 (Chrome 브라우저로 위장)
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'mobile': False
    }
)

# 학교 홈페이지 메인 주소
KMOU_MAIN_URL = "https://www.kmou.ac.kr"

def collect_university_info(target_url, filename):
    """
    학교 홈페이지에서 데이터를 수집하여 텍스트 파일로 저장합니다.
    보안 강화로 인한 차단을 우회하기 위해 cloudscraper를 사용합니다.
    """
    print(f"📡 데이터 수집 중: [{filename}] ...")
    
    # 파일 경로 설정
    file_path = data_dir / f"{filename}.txt"
    
    try:
        # 헤더 설정 (실제 브라우저처럼 보이도록)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': KMOU_MAIN_URL,
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
        }
        
        # CloudScraper로 요청 (챌린지 자동 우회)
        response = scraper.get(target_url, headers=headers, timeout=30)
        
        # HTTP 상태 코드 확인
        if response.status_code == 403:
            error_msg = f"현재 보안 점검으로 인해 정보를 가져올 수 없습니다. 대신 다음 링크를 확인해 주세요: {target_url}"
            logger.warning(f"403 Forbidden 발생 ({filename}): {target_url}")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(error_msg)
            print(f"  ⚠️ 403 에러 - 안내 문구 저장: {file_path.name}")
            return
        
        if response.status_code == 404:
            error_msg = f"현재 보안 점검으로 인해 정보를 가져올 수 없습니다. 대신 다음 링크를 확인해 주세요: {target_url}"
            logger.warning(f"404 Not Found 발생 ({filename}): {target_url}")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(error_msg)
            print(f"  ⚠️ 404 에러 - 안내 문구 저장: {file_path.name}")
            return
        
        # 응답 상태 코드 확인
        response.raise_for_status()
        
        # 식단 페이지인 경우 특별 처리
        if filename == "cafeteria_menu":
            try:
                # HTML 파싱
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 식단 정보 추출 시도
                # 실제 페이지 구조에 맞게 수정 필요
                content = soup.get_text(separator='\n', strip=True)
                
                # 내용이 너무 짧으면 파싱 실패로 간주
                if len(content) < 100:
                    raise ValueError("식단 정보 추출 실패")
                
                # 파일 저장
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                
                print(f"  ✨ 성공적으로 저장됨: {file_path.name}")
                
            except Exception as parse_error:
                error_msg = "식단 정보를 불러오는 중 오류가 발생했습니다."
                logger.error(f"식단 페이지 파싱 실패 ({filename}): {parse_error}")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"{error_msg}\n원본 URL: {target_url}")
                print(f"  ⚠️ 파싱 실패 - 안내 문구 저장: {file_path.name}")
        else:
            # 일반 페이지 처리
            # HTML을 텍스트로 변환
            soup = BeautifulSoup(response.text, 'html.parser')
            content = soup.get_text(separator='\n', strip=True)
            
            # 파일 저장
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            print(f"  ✨ 성공적으로 저장됨: {file_path.name}")
        
    except Exception as e:
        # 기타 예외 처리 (프로그램 중단하지 않고 로그만 남김)
        error_msg = f"현재 보안 점검으로 인해 정보를 가져올 수 없습니다. 대신 다음 링크를 확인해 주세요: {target_url}"
        logger.error(f"수집 실패 ({filename}): {e}")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
        print(f"  ❌ 수집 실패 - 안내 문구 저장: {file_path.name}")

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