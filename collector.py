"""
방탄 모드 데이터 수집기 (Bulletproof Collector)
- 보안 우회 심화: 세션 유지, 랜덤 딜레이, 최신 헤더
- 계층적 예외 처리: 모든 에러 상황 대응
- 우아한 실패: 크래시 없이 친절한 안내 문구 저장
"""

import os
import sys  # <--- [중요] 아까 에러를 해결하는 핵심 열쇠입니다!
import time
import random
import logging
from pathlib import Path

# 외부 라이브러리
import cloudscraper
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('collector.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =========================
# 전역 설정
# =========================

# 현재 파일의 절대 경로를 가져와서 'university_data' 폴더 경로 확정
current_dir = Path(__file__).parent.absolute()
data_dir = current_dir / "university_data"

# 폴더 강제 생성 및 권한 확인
try:
    data_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"데이터 저장 경로 확보: {data_dir}")
except Exception as e:
    logger.warning(f"폴더 생성 중 경고 (무시 가능): {e}")

# 학교 홈페이지 메인 주소
KMOU_MAIN_URL = "https://www.kmou.ac.kr"

# 최신 Chrome User-Agent (2025년 1월 기준)
LATEST_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# =========================
# CloudScraper 세션 초기화 (쿠키 유지)
# =========================

def create_scraper_session():
    """
    CloudScraper 세션을 생성하여 쿠키를 유지합니다.
    """
    try:
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            },
            delay=random.uniform(1, 2)  # 초기 딜레이
        )
        logger.info("CloudScraper 세션 생성 완료")
        return scraper
    except Exception as e:
        logger.error(f"CloudScraper 세션 생성 실패: {e}")
        raise

# 전역 세션 (쿠키 유지)
scraper_session = create_scraper_session()

# =========================
# 헤더 생성 함수
# =========================

def get_headers(referer_url: str = None) -> dict:
    """
    실제 브라우저처럼 보이는 헤더를 생성합니다.
    
    Args:
        referer_url: Referer 헤더에 사용할 URL (기본값: KMOU_MAIN_URL)
    
    Returns:
        헤더 딕셔너리
    """
    headers = {
        'User-Agent': LATEST_CHROME_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': referer_url or KMOU_MAIN_URL,
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    return headers

# =========================
# 우아한 실패 메시지
# =========================

FALLBACK_MESSAGE = (
    "⚠️ 현재 학교 홈페이지 보안 점검으로 인해 실시간 정보를 가져오지 못했습니다. "
    "정확한 내용은 학교 홈페이지를 참고해주세요."
)

# =========================
# 계층적 예외 처리 함수
# =========================

def save_fallback_message(file_path: Path, url: str = None):
    """
    실패 시 친절한 안내 문구를 저장합니다.
    
    Args:
        file_path: 저장할 파일 경로
        url: 원본 URL (선택)
    """
    try:
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

def safe_request(url: str, filename: str, max_retries: int = 2):
    """
    보안 우회 및 예외 처리가 강화된 안전한 HTTP 요청을 수행합니다.
    
    Args:
        url: 요청할 URL
        filename: 로그용 파일명
        max_retries: 최대 재시도 횟수
    
    Returns:
        response 객체 또는 None (실패 시)
    """
    headers = get_headers()
    
    for attempt in range(max_retries + 1):
        try:
            # 요청 전 랜덤 딜레이 (인간의 접속 패턴 모사)
            if attempt > 0:
                delay = random.uniform(2, 4)  # 재시도 시 더 긴 딜레이
                logger.info(f"[{filename}] 재시도 전 대기: {delay:.2f}초")
                time.sleep(delay)
            else:
                delay = random.uniform(1, 3)
                time.sleep(delay)
            
            logger.info(f"[{filename}] 요청 시도 {attempt + 1}/{max_retries + 1}: {url}")
            
            # CloudScraper로 요청 (챌린지 자동 우회, 세션으로 쿠키 유지)
            response = scraper_session.get(url, headers=headers, timeout=30)
            
            # HTTP 상태 코드 확인
            if response.status_code == 403:
                logger.warning(f"[{filename}] 403 Forbidden 발생")
                return None
            elif response.status_code == 404:
                logger.warning(f"[{filename}] 404 Not Found 발생")
                return None
            elif response.status_code != 200:
                logger.warning(f"[{filename}] HTTP {response.status_code} 발생")
                if attempt < max_retries:
                    continue
                return None
            
            response.raise_for_status()
            logger.info(f"[{filename}] 요청 성공 (상태 코드: {response.status_code})")
            return response
            
        except cloudscraper.exceptions.CloudflareChallengeError as e:
            logger.error(f"[{filename}] Cloudflare 챌린지 실패: {e}")
            if attempt < max_retries:
                logger.info(f"[{filename}] 재시도 예정...")
                continue
            return None
            
        except Exception as e:
            # requests.exceptions.Timeout 포함
            error_type = type(e).__name__
            logger.error(f"[{filename}] 요청 실패 ({error_type}): {e}")
            if attempt < max_retries:
                logger.info(f"[{filename}] 재시도 예정...")
                continue
            return None
    
    return None

# =========================
# 안전한 HTML 파싱 함수
# =========================

def safe_parse_html(response_text: str, filename: str) -> str:
    """
    HTML을 안전하게 파싱하여 텍스트로 변환합니다.
    
    Args:
        response_text: HTML 텍스트
        filename: 로그용 파일명
    
    Returns:
        파싱된 텍스트 또는 빈 문자열
    """
    try:
        soup = BeautifulSoup(response_text, 'html.parser')
        content = soup.get_text(separator='\n', strip=True)
        
        # 내용이 너무 짧으면 파싱 실패로 간주
        if len(content) < 50:
            logger.warning(f"[{filename}] 파싱된 내용이 너무 짧음 ({len(content)}자)")
            return ""
        
        logger.info(f"[{filename}] HTML 파싱 성공 ({len(content)}자)")
        return content
        
    except AttributeError as e:
        logger.error(f"[{filename}] HTML 구조 파싱 실패 (AttributeError): {e}")
        return ""
    except IndexError as e:
        logger.error(f"[{filename}] HTML 구조 파싱 실패 (IndexError): {e}")
        return ""
    except Exception as e:
        logger.error(f"[{filename}] HTML 파싱 실패: {e}")
        return ""

# =========================
# 식단 정보 수집 함수
# =========================

def fetch_meal(url: str, file_path: Path) -> bool:
    """
    식단 정보를 수집합니다.
    
    Args:
        url: 식단 페이지 URL
        file_path: 저장할 파일 경로
    
    Returns:
        성공 여부
    """
    logger.info(f"[식단] 수집 시작: {url}")
    
    try:
        response = safe_request(url, "식단")
        
        if response is None:
            save_fallback_message(file_path, url)
            return False
        
        # HTML 파싱
        content = safe_parse_html(response.text, "식단")
        
        if not content:
            logger.warning("[식단] 파싱된 내용이 없음")
            save_fallback_message(file_path, url)
            return False
        
        # 파일 저장
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"[식단] 성공적으로 저장됨: {file_path.name}")
        return True
        
    except Exception as e:
        logger.error(f"[식단] 수집 실패: {e}")
        save_fallback_message(file_path, url)
        return False

# =========================
# 공지사항 수집 함수
# =========================

def fetch_notice(url: str, file_path: Path) -> bool:
    """
    공지사항 정보를 수집합니다.
    
    Args:
        url: 공지사항 페이지 URL
        file_path: 저장할 파일 경로
    
    Returns:
        성공 여부
    """
    logger.info(f"[공지사항] 수집 시작: {url}")
    
    try:
        response = safe_request(url, "공지사항")
        
        if response is None:
            save_fallback_message(file_path, url)
            return False
        
        # HTML 파싱
        content = safe_parse_html(response.text, "공지사항")
        
        if not content:
            logger.warning("[공지사항] 파싱된 내용이 없음")
            save_fallback_message(file_path, url)
            return False
        
        # 파일 저장
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"[공지사항] 성공적으로 저장됨: {file_path.name}")
        return True
        
    except Exception as e:
        logger.error(f"[공지사항] 수집 실패: {e}")
        save_fallback_message(file_path, url)
        return False

# =========================
# 학사 안내 수집 함수
# =========================

def fetch_academic_guide(url: str, file_path: Path) -> bool:
    """
    학사 안내 정보를 수집합니다.
    
    Args:
        url: 학사 안내 페이지 URL
        file_path: 저장할 파일 경로
    
    Returns:
        성공 여부
    """
    logger.info(f"[학사안내] 수집 시작: {url}")
    
    try:
        response = safe_request(url, "학사안내")
        
        if response is None:
            save_fallback_message(file_path, url)
            return False
        
        # HTML 파싱
        content = safe_parse_html(response.text, "학사안내")
        
        if not content:
            logger.warning("[학사안내] 파싱된 내용이 없음")
            save_fallback_message(file_path, url)
            return False
        
        # 파일 저장
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"[학사안내] 성공적으로 저장됨: {file_path.name}")
        return True
        
    except Exception as e:
        logger.error(f"[학사안내] 수집 실패: {e}")
        save_fallback_message(file_path, url)
        return False

# =========================
# 장학금 안내 수집 함수
# =========================

def fetch_scholarship_guide(url: str, file_path: Path) -> bool:
    """
    장학금 안내 정보를 수집합니다.
    
    Args:
        url: 장학금 안내 페이지 URL
        file_path: 저장할 파일 경로
    
    Returns:
        성공 여부
    """
    logger.info(f"[장학금] 수집 시작: {url}")
    
    try:
        response = safe_request(url, "장학금")
        
        if response is None:
            save_fallback_message(file_path, url)
            return False
        
        # HTML 파싱
        content = safe_parse_html(response.text, "장학금")
        
        if not content:
            logger.warning("[장학금] 파싱된 내용이 없음")
            save_fallback_message(file_path, url)
            return False
        
        # 파일 저장
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"[장학금] 성공적으로 저장됨: {file_path.name}")
        return True
        
    except Exception as e:
        logger.error(f"[장학금] 수집 실패: {e}")
        save_fallback_message(file_path, url)
        return False

# =========================
# 행사/세미나 수집 함수
# =========================

def fetch_events_seminar(url: str, file_path: Path) -> bool:
    """
    행사/세미나 정보를 수집합니다.
    
    Args:
        url: 행사/세미나 페이지 URL
        file_path: 저장할 파일 경로
    
    Returns:
        성공 여부
    """
    logger.info(f"[행사/세미나] 수집 시작: {url}")
    
    try:
        response = safe_request(url, "행사/세미나")
        
        if response is None:
            save_fallback_message(file_path, url)
            return False
        
        # HTML 파싱
        content = safe_parse_html(response.text, "행사/세미나")
        
        if not content:
            logger.warning("[행사/세미나] 파싱된 내용이 없음")
            save_fallback_message(file_path, url)
            return False
        
        # 파일 저장
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"[행사/세미나] 성공적으로 저장됨: {file_path.name}")
        return True
        
    except Exception as e:
        logger.error(f"[행사/세미나] 수집 실패: {e}")
        save_fallback_message(file_path, url)
        return False

# =========================
# 메인 수집 함수 (통합)
# =========================

def collect_university_info(target_url: str, filename: str) -> bool:
    """
    학교 홈페이지에서 데이터를 수집하여 텍스트 파일로 저장합니다.
    방탄 모드: 보안 우회 심화 + 계층적 예외 처리 + 우아한 실패
    
    Args:
        target_url: 수집할 URL
        filename: 저장할 파일명 (확장자 제외)
    
    Returns:
        성공 여부
    """
    file_path = data_dir / f"{filename}.txt"
    
    try:
        # 파일명에 따라 적절한 함수 호출
        if filename == "cafeteria_menu":
            return fetch_meal(target_url, file_path)
        elif filename == "notice_general":
            return fetch_notice(target_url, file_path)
        elif filename == "academic_guide":
            return fetch_academic_guide(target_url, file_path)
        elif filename == "scholarship_guide":
            return fetch_scholarship_guide(target_url, file_path)
        elif filename == "events_seminar":
            return fetch_events_seminar(target_url, file_path)
        else:
            # 기본 처리 (일반 페이지)
            logger.info(f"[{filename}] 수집 시작: {target_url}")
            response = safe_request(target_url, filename)
            
            if response is None:
                save_fallback_message(file_path, target_url)
                return False
            
            content = safe_parse_html(response.text, filename)
            
            if not content:
                save_fallback_message(file_path, target_url)
                return False
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            logger.info(f"[{filename}] 성공적으로 저장됨: {file_path.name}")
            return True
            
    except Exception as e:
        logger.error(f"[{filename}] 치명적 오류 발생: {e}")
        save_fallback_message(file_path, target_url)
        return False

# =========================
# 메인 실행 로직
# =========================

if __name__ == "__main__":
    try:
        logger.info("=" * 60)
        logger.info("방탄 모드 데이터 수집기 시작")
        logger.info("=" * 60)
        
        # KMOU 게시판 목록
        urls_to_crawl = {
            "notice_general": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=2032&bbsId=10373",
            "academic_guide": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=2033&bbsId=11786",
            "scholarship_guide": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=5691&bbsId=10004365",
            "events_seminar": "https://www.kmou.ac.kr/kmou/na/ntt/selectNttList.do?mi=2034&bbsId=10375",
            "cafeteria_menu": "https://www.kmou.ac.kr/coop/dv/dietView/selectDietDateView.do?mi=1189"
        }
        
        success_count = 0
        total_count = len(urls_to_crawl)
        
        for name, url in urls_to_crawl.items():
            try:
                if collect_university_info(url, name):
                    success_count += 1
            except Exception as e:
                logger.error(f"[{name}] 예상치 못한 오류: {e}")
                # 파일 경로 설정 및 안내 문구 저장
                file_path = data_dir / f"{name}.txt"
                save_fallback_message(file_path, url)
            
            # 요청 사이 랜덤 딜레이 (인간의 접속 패턴 모사)
            if name != list(urls_to_crawl.keys())[-1]:  # 마지막 항목이 아니면
                delay = random.uniform(1, 3)
                logger.info(f"다음 요청 전 대기: {delay:.2f}초")
                time.sleep(delay)
        
        logger.info("=" * 60)
        logger.info(f"수집 작업 완료: {success_count}/{total_count} 성공")
        logger.info("=" * 60)
        print(f"\n🚀 모든 수집 작업이 끝났습니다. (성공: {success_count}/{total_count})")
        print("이제 ingest.py를 실행해 보세요!")
        
    except Exception as e:
        # 전체 프로그램이 크래시되지 않도록 최상위 예외 처리
        logger.error(f"치명적 오류 발생: {e}")
        import traceback
        logger.error(traceback.format_exc())
        print(f"\n⚠️ 수집 작업 중 오류가 발생했습니다: {e}")
        print("로그 파일(collector.log)을 확인해주세요.")
        # Exit code 0으로 정상 종료 (서버 실행이 막히지 않도록)
        sys.exit(0)
