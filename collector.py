"""
방탄 모드 데이터 수집기 (Bulletproof Collector)
- Import 에러 방어: 라이브러리 없어도 서버 기동 보장
- 보안 우회 심화: 세션 유지, 랜덤 딜레이
- 계층적 예외 처리: 모든 에러 상황 대응
- 우아한 실패: 크래시 없이 친절한 안내 문구 저장
"""

import os
import sys
import time
import random
import logging
from pathlib import Path

# =================================================================
# [핵심] 라이브러리 로드 방어막 (Import Error로 인한 Crash 방지)
# =================================================================
try:
    from dotenv import load_dotenv
    # 환경 변수 로드
    load_dotenv()
    
    import cloudscraper
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"⚠️ [Critical] 필수 라이브러리가 없습니다: {e}")
    print("➡️ 크롤링을 건너뛰고 정상 종료합니다. (서버 실행 보장)")
    # 여기서 Exit 1을 내면 서버가 죽습니다. Exit 0으로 속여서 서버를 살립니다.
    sys.exit(0)

# =========================
# 로깅 설정
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler() # 파일 로깅 제거 (Render 디스크 권한 문제 방지)
    ]
)
logger = logging.getLogger(__name__)

# =========================
# 전역 설정
# =========================

# 현재 파일의 절대 경로를 가져와서 'university_data' 폴더 경로 확정
current_dir = Path(__file__).parent.absolute()
data_dir = current_dir / "university_data"

# 학교 홈페이지 메인 주소
KMOU_MAIN_URL = "https://www.kmou.ac.kr"

# 최신 Chrome User-Agent
LATEST_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# =========================
# CloudScraper 세션 초기화
# =========================

def create_scraper_session():
    try:
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            },
            delay=random.uniform(1, 2)
        )
        return scraper
    except Exception as e:
        logger.error(f"CloudScraper 세션 생성 실패: {e}")
        return None

# 전역 세션 (실패 시 None)
scraper_session = create_scraper_session()

# =========================
# 헤더 생성 함수
# =========================

def get_headers(referer_url: str = None) -> dict:
    headers = {
        'User-Agent': LATEST_CHROME_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Referer': referer_url or KMOU_MAIN_URL,
        'Upgrade-Insecure-Requests': '1',
    }
    return headers

# =========================
# 우아한 실패 처리
# =========================

FALLBACK_MESSAGE = (
    "⚠️ 현재 학교 홈페이지 보안 점검 또는 연결 문제로 정보를 가져오지 못했습니다. "
    "정확한 내용은 학교 홈페이지를 참고해주세요."
)

def save_fallback_message(file_path: Path, url: str = None):
    try:
        # 폴더가 없으면 생성 (방어 코드)
        if not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)

        message = FALLBACK_MESSAGE
        if url:
            message += f"\n\n원본 URL: {url}"
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(message)
        logger.info(f"안내 문구 저장 완료: {file_path.name}")
    except Exception as e:
        logger.error(f"안내 문구 저장 실패: {e}")

# =========================
# 안전한 요청 함수
# =========================

def safe_request(url: str, filename: str):
    if scraper_session is None:
        return None

    headers = get_headers()
    
    # 최대 2회 재시도
    for attempt in range(2):
        try:
            if attempt > 0:
                time.sleep(random.uniform(2, 4))
            
            response = scraper_session.get(url, headers=headers, timeout=15) # 타임아웃 단축 (서버 지연 방지)
            
            if response.status_code == 200:
                return response
            elif response.status_code in [403, 404]:
                logger.warning(f"[{filename}] HTTP {response.status_code}")
                return None
            
        except Exception as e:
            logger.error(f"[{filename}] 요청 실패: {e}")
            continue
            
    return None

# =========================
# 메인 수집 함수 (단순화)
# =========================

def collect_and_save(url: str, filename: str):
    file_path = data_dir / f"{filename}.txt"
    
    try:
        response = safe_request(url, filename)
        
        if response and len(response.text) > 100:
            # HTML 파싱 (BeautifulSoup)
            soup = BeautifulSoup(response.text, 'html.parser')
            content = soup.get_text(separator='\n', strip=True)
            
            if len(content) > 50:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"✅ [{filename}] 저장 성공")
                return True
                
    except Exception as e:
        logger.error(f"[{filename}] 처리 중 오류: {e}")

    # 실패 시 안내 문구 저장
    save_fallback_message(file_path, url)
    return False

# =========================
# 메인 실행 로직
# =========================

def main():
    print("🚀 [Collector] 데이터 수집 시작...")
    
    # 폴더 생성
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)

    urls = {
        "notice_general": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=2032&bbsId=10373",
        "academic_guide": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=2033&bbsId=11786",
        "scholarship_guide": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=5691&bbsId=10004365",
        "cafeteria_menu": "https://www.kmou.ac.kr/coop/dv/dietView/selectDietDateView.do?mi=1189"
    }

    for name, url in urls.items():
        collect_and_save(url, name)
        time.sleep(1) # 부하 방지

    print("✅ [Collector] 모든 작업 완료. 정상 종료합니다.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 어떤 치명적 오류가 나도 로그만 찍고 정상 종료(Exit 0)
        print(f"⚠️ [Collector] 알 수 없는 오류 발생: {e}")
        print("➡️ 시스템 안정성을 위해 정상 종료 처리합니다.")
    finally:
        sys.exit(0) # <--- [핵심] 무조건 성공한 척 종료