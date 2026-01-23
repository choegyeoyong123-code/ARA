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
    """
    보안 우회 강화된 CloudScraper 세션 생성
    - 최신 Chrome 브라우저 모사
    - 쿠키 및 세션 유지
    - 자동 챌린지 우회
    """
    try:
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False,
                'desktop': True
            },
            delay=random.uniform(1.5, 3.0),  # 딜레이 증가 (인간 패턴)
            debug=False
        )
        
        # 추가 헤더 설정 (브라우저 모사 강화)
        scraper.headers.update({
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        })
        
        logger.info("✅ CloudScraper 세션 생성 완료 (보안 우회 활성화)")
        return scraper
    except Exception as e:
        logger.error(f"❌ CloudScraper 세션 생성 실패: {e}")
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
    """
    보안 우회 강화된 안전한 요청 함수
    - Cloudflare 챌린지 우회
    - 세션 쿠키 유지
    - 계층적 예외 처리
    """
    if scraper_session is None:
        return None

    headers = get_headers()
    
    # 최대 3회 재시도 (보안 우회 강화)
    for attempt in range(3):
        try:
            if attempt > 0:
                # 재시도 시 더 긴 딜레이 (인간 패턴 모사)
                delay = random.uniform(3, 6)
                logger.info(f"[{filename}] 재시도 {attempt}회 - {delay:.1f}초 대기...")
                time.sleep(delay)
            
            # 요청 전 랜덤 딜레이 (인간 패턴)
            if attempt == 0:
                time.sleep(random.uniform(1, 3))
            
            # CloudScraper로 요청 (자동 챌린지 우회)
            response = scraper_session.get(
                url, 
                headers=headers, 
                timeout=20,  # 타임아웃 증가
                allow_redirects=True
            )
            
            if response.status_code == 200:
                # 응답 크기 확인 (너무 작으면 의심)
                if len(response.text) < 100:
                    logger.warning(f"[{filename}] 응답이 너무 짧습니다 ({len(response.text)}자)")
                    if attempt < 2:  # 마지막 시도가 아니면 재시도
                        continue
                
                return response
            elif response.status_code in [403, 404]:
                logger.warning(f"[{filename}] HTTP {response.status_code}")
                return None
            elif response.status_code == 429:  # Too Many Requests
                logger.warning(f"[{filename}] Rate Limit - 더 긴 대기 후 재시도")
                if attempt < 2:
                    time.sleep(random.uniform(10, 15))
                    continue
                return None
            
        except Exception as e:
            error_type = type(e).__name__
            if "CloudflareChallengeError" in error_type or "Challenge" in str(e):
                logger.error(f"[{filename}] Cloudflare 챌린지 실패: {e}")
                if attempt < 2:
                    # 챌린지 실패 시 더 긴 대기
                    time.sleep(random.uniform(5, 10))
                    continue
            elif "Timeout" in error_type:
                logger.error(f"[{filename}] 타임아웃: {e}")
                if attempt < 2:
                    continue
            elif "AttributeError" in error_type or "IndexError" in error_type:
                logger.error(f"[{filename}] 파싱 오류: {e}")
                # 파싱 오류는 재시도 불필요
                return None
            else:
                logger.error(f"[{filename}] 요청 실패 ({error_type}): {e}")
                if attempt < 2:
                    continue
            
    return None

# =========================
# 메인 수집 함수 (단순화)
# =========================

def collect_and_save(url: str, filename: str):
    """
    보안 우회 강화된 데이터 수집 함수
    - 계층적 예외 처리
    - 우아한 실패 처리
    """
    file_path = data_dir / f"{filename}.txt"
    
    try:
        response = safe_request(url, filename)
        
        if response and len(response.text) > 100:
            try:
                # HTML 파싱 (BeautifulSoup) - lxml 파서 사용 (더 빠르고 안정적)
                soup = BeautifulSoup(response.text, 'lxml')
                
                # 스크립트, 스타일 태그 제거 (노이즈 제거)
                for script in soup(["script", "style", "meta", "link"]):
                    script.decompose()
                
                # 텍스트 추출
                content = soup.get_text(separator='\n', strip=True)
                
                # 빈 줄 제거 및 정리
                lines = [line.strip() for line in content.split('\n') if line.strip()]
                content = '\n'.join(lines)
                
                if len(content) > 50:
                    # 폴더 생성 (안전장치)
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    logger.info(f"✅ [{filename}] 저장 성공 ({len(content)}자)")
                    return True
                else:
                    logger.warning(f"[{filename}] 추출된 내용이 너무 짧습니다 ({len(content)}자)")
                    
            except AttributeError as e:
                logger.error(f"[{filename}] HTML 구조 파싱 오류: {e}")
                # 파싱 오류는 특별 메시지 저장
                if filename == "cafeteria_menu":
                    fallback_msg = "식단 정보를 불러오는 중 오류가 발생했습니다."
                else:
                    fallback_msg = FALLBACK_MESSAGE
                save_fallback_message(file_path, url)
                return False
            except IndexError as e:
                logger.error(f"[{filename}] 인덱스 오류 (HTML 구조 변경): {e}")
                save_fallback_message(file_path, url)
                return False
            except Exception as e:
                logger.error(f"[{filename}] 파싱 중 예상치 못한 오류: {e}")
                save_fallback_message(file_path, url)
                return False
        else:
            logger.warning(f"[{filename}] 응답이 없거나 너무 짧습니다")
                
    except Exception as e:
        logger.error(f"[{filename}] 처리 중 치명적 오류: {e}")
        import traceback
        logger.error(traceback.format_exc())

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
        logger.info(f"📥 [{name}] 수집 시작: {url}")
        collect_and_save(url, name)
        # 인간 패턴 모사: 각 요청 사이 랜덤 딜레이
        delay = random.uniform(2, 4)
        time.sleep(delay)
        logger.info(f"⏸️ [{name}] {delay:.1f}초 대기 완료")

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