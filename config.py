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
    """
    Streamlit secrets 로드
    우선순위:
    1) st.secrets (Streamlit Cloud/로컬)
    2) 프로젝트 .streamlit/secrets.toml
    3) 홈 디렉토리 ~/.streamlit/secrets.toml
    """
    # 1) Streamlit Secrets API
    try:
        import streamlit as st  # type: ignore
        if hasattr(st, "secrets"):
            if hasattr(st.secrets, "to_dict"):
                secrets = st.secrets.to_dict()
            else:
                secrets = dict(st.secrets)
            if secrets:
                return secrets
    except Exception:
        pass

    # 2) 로컬 파일
    secrets_paths = [
        BASE_DIR / ".streamlit" / "secrets.toml",
        Path.home() / ".streamlit" / "secrets.toml",
    ]
    for secrets_path in secrets_paths:
        if secrets_path.exists():
            try:
                import toml
                return toml.load(secrets_path)
            except Exception:
                continue

    return {}

_secrets = _load_streamlit_secrets()


def get_secret(key: str, default: str = "") -> str:
    """환경변수 → Streamlit secrets → 기본값 순으로 조회"""
    if key in os.environ:
        return os.environ.get(key, default)
    if key in _secrets:
        return _secrets.get(key, default)

    # 섹션형 secrets 지원 (예: [secrets], [default])
    for section in ("secrets", "default"):
        section_map = _secrets.get(section)
        if isinstance(section_map, dict) and key in section_map:
            return section_map.get(key, default)

    return default


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

ALIEXPRESS_APP_KEY = get_secret("ALIEXPRESS_APP_KEY")
ALIEXPRESS_APP_SECRET = get_secret("ALIEXPRESS_APP_SECRET")
ALIEXPRESS_TRACKING_ID = get_secret("ALIEXPRESS_TRACKING_ID")
ALIEXPRESS_LANGUAGE = get_secret("ALIEXPRESS_LANGUAGE", "EN")
ALIEXPRESS_CURRENCY = get_secret("ALIEXPRESS_CURRENCY", "USD")

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
MAX_DAILY_PRODUCTS = 12        # 하루 최대 상품 소싱 수 (안전 제한)

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

제품번호: {product_code}
상품명: {product_name}
{bio_text}
{affiliate_text}

좋은 하루 보내세요! 💕"""

# ──────────────────────────────────────────────
# AliExpress 기본 키워드 (자동 실행용)
# ──────────────────────────────────────────────
ALIEXPRESS_DEFAULT_KEYWORD = get_secret("ALIEXPRESS_DEFAULT_KEYWORD", "")

# ──────────────────────────────────────────────
# Linktree/Webhook 설정
# ──────────────────────────────────────────────
LINKTREE_MODE = get_secret("LINKTREE_MODE", "webhook")  # webhook | queue | disabled
LINKTREE_WEBHOOK_URL = get_secret("LINKTREE_WEBHOOK_URL")
LINKTREE_WEBHOOK_SECRET = get_secret("LINKTREE_WEBHOOK_SECRET")

# ──────────────────────────────────────────────
# 트렌드 키워드 설정 (한국 트렌드 자동 소싱)
# ──────────────────────────────────────────────
TREND_SOURCE = get_secret("TREND_SOURCE", "google_trends")  # google_trends | fallback
TREND_GEO = get_secret("TREND_GEO", "KR")
TREND_MAX_ITEMS = int(get_secret("TREND_MAX_ITEMS", "20") or 20)

_fallback_raw = get_secret("TREND_FALLBACK_KEYWORDS", "")
if _fallback_raw:
    TREND_FALLBACK_KEYWORDS = [x.strip() for x in _fallback_raw.split(",") if x.strip()]
else:
    TREND_FALLBACK_KEYWORDS = [
        "가성비 전자제품",
        "주방 꿀템",
        "홈카페 용품",
        "운동용품",
        "스킨케어",
        "미니 가전",
        "정리 수납",
        "자동차 용품",
        "캠핑 용품",
        "반려동물 용품",
    ]

# ──────────────────────────────────────────────
# Notion 링크 페이지 설정
# ──────────────────────────────────────────────
NOTION_TOKEN = get_secret("NOTION_TOKEN")
NOTION_DATABASE_ID = get_secret("NOTION_DATABASE_ID")
NOTION_PUBLIC_URL = get_secret("NOTION_PUBLIC_URL")  # 인스타 프로필에 걸어둘 노션 공개 링크

NOTION_PROP_NAME = get_secret("NOTION_PROP_NAME", "Name")  # title
NOTION_PROP_CODE = get_secret("NOTION_PROP_CODE", "Product Code")  # rich_text
NOTION_PROP_LINK = get_secret("NOTION_PROP_LINK", "Link")  # url
NOTION_PROP_SOURCE = get_secret("NOTION_PROP_SOURCE", "Source")  # select
NOTION_PROP_PRICE = get_secret("NOTION_PROP_PRICE", "Price")  # rich_text/number
NOTION_PROP_IMAGE = get_secret("NOTION_PROP_IMAGE", "Image")  # url
