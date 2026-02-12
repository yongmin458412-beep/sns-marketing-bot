"""
config.py - 중앙 설정 관리 모듈
모든 API 키와 설정값을 환경변수 / secrets.toml / GitHub Secrets에서 불러옵니다.
"""

import os
import json
from pathlib import Path

# ──────────────────────────────────────────────
# 기본 경로 설정
# ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
SESSIONS_DIR = BASE_DIR / "sessions"
DOWNLOADS_DIR = BASE_DIR / "downloads"

# 디렉토리 자동 생성
for d in [DATA_DIR, ASSETS_DIR, SESSIONS_DIR, DOWNLOADS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────
# Streamlit secrets 호환 로드
# ──────────────────────────────────────────────
def _load_streamlit_secrets() -> dict:
    """Streamlit secrets.toml이 있으면 파싱하여 반환"""
    secrets_path = BASE_DIR / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        try:
            import toml
            return toml.load(secrets_path)
        except ImportError:
            pass
    return {}

_secrets = _load_streamlit_secrets()


def get_secret(key: str, default: str = "") -> str:
    """환경변수 → Streamlit secrets → 기본값 순으로 조회"""
    return os.environ.get(key, _secrets.get(key, default))


# ──────────────────────────────────────────────
# API Keys & Credentials
# ──────────────────────────────────────────────
OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get_secret("TELEGRAM_CHAT_ID")

INSTAGRAM_USERNAME = get_secret("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = get_secret("INSTAGRAM_PASSWORD")

COUPANG_ACCESS_KEY = get_secret("COUPANG_ACCESS_KEY")
COUPANG_SECRET_KEY = get_secret("COUPANG_SECRET_KEY")
COUPANG_PARTNER_ID = get_secret("COUPANG_PARTNER_ID")

# ──────────────────────────────────────────────
# 크롤링 / 필터 설정
# ──────────────────────────────────────────────
COUPANG_GOLDBOX_URL = "https://www.coupang.com/np/goldbox"
COUPANG_RANKING_URL = "https://www.coupang.com/np/categories/393760"

# 바이럴 영상 필터 기준
MIN_VIEW_COUNT = 100_000       # 최소 조회수
MIN_LIKE_COUNT = 5_000         # 최소 좋아요
MIN_DURATION = 15              # 최소 영상 길이 (초)
MAX_DURATION = 50              # 최대 영상 길이 (초)

# ──────────────────────────────────────────────
# 영상 편집 설정
# ──────────────────────────────────────────────
SPEED_FACTOR = 1.15            # 재생 속도 배율
CROP_ZOOM = 0.05               # 확대(Zoom-in) 비율 (5%)
ORIGINAL_AUDIO_VOLUME = 0.30   # 원본 오디오 볼륨 (30%)
HOOK_DURATION = 3              # 후킹 자막 표시 시간 (초)
HOOK_FONT_SIZE = 48            # 후킹 자막 폰트 크기
HOOK_FONT_COLOR = "yellow"     # 후킹 자막 색상

# ──────────────────────────────────────────────
# 인스타그램 설정
# ──────────────────────────────────────────────
INSTAGRAM_SESSION_FILE = SESSIONS_DIR / "ig_session.json"
COMMENT_POLL_INTERVAL = 60     # 댓글 모니터링 주기 (초)
MAX_DM_PER_HOUR = 20           # 시간당 최대 DM 발송 수 (안전 제한)

# ──────────────────────────────────────────────
# 자동 실행 설정
# ──────────────────────────────────────────────
DAILY_RUN_COUNT = 3            # 하루 실행 횟수
MAX_PRODUCTS_PER_RUN = 5       # 실행당 최대 처리 상품 수

# ──────────────────────────────────────────────
# 데이터베이스 설정
# ──────────────────────────────────────────────
DB_PATH = DATA_DIR / "bot_database.db"

# ──────────────────────────────────────────────
# GPT 프롬프트 템플릿
# ──────────────────────────────────────────────
VISION_PROMPT = """이 제품 이미지를 분석하여 다음 정보를 JSON 형식으로 알려주세요:
{
  "product_name": "정확한 영문 제품명",
  "keywords": ["해외 쇼핑몰 검색 키워드 1", "키워드 2", "키워드 3"]
}
"""

HOOK_TEXT_PROMPT = """다음 제품에 대한 한국어 '후킹 문구'를 1개 만들어주세요.
조건: 15자 이내, MZ세대 말투, 호기심 유발, 이모지 1개 포함
제품명: {product_name}
"""

CAPTION_PROMPT = """다음 제품에 대한 인스타그램 릴스 본문을 작성해주세요.
조건:
- 공감형 문체 (2~3줄)
- 해시태그 10개 포함
- 쿠팡 링크 유도 문구 포함
제품명: {product_name}
"""

REPLY_TEMPLATES = [
    "정보 DM으로 보내드렸어요! 🔥",
    "DM 확인해주세요! 💌",
    "요청하신 정보 DM으로 보냈습니다! ✨",
    "DM 드렸어요~ 확인해보세요! 😊",
    "자세한 정보 DM으로 전달했어요! 🎁",
]

DM_TEMPLATE = """안녕하세요! 😊
문의하신 제품 정보입니다!

👉 {product_name}
🔗 {affiliate_link}

좋은 하루 보내세요! 💕"""
