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
LOG_FILE = DATA_DIR / "pipeline.log"

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


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


# ──────────────────────────────────────────────
# API Keys & Credentials
# ──────────────────────────────────────────────
OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get_secret("TELEGRAM_CHAT_ID")

INSTAGRAM_USERNAME = get_secret("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = get_secret("INSTAGRAM_PASSWORD")

# Instagram Graph API (공식 API)
IG_API_MODE = get_secret("IG_API_MODE", "graph")  # graph | instagrapi | disabled
IG_GRAPH_API_VERSION = get_secret("IG_GRAPH_API_VERSION", "v20.0")
IG_GRAPH_HOST = get_secret("IG_GRAPH_HOST", "graph.facebook.com")
IG_MESSAGE_HOST = get_secret("IG_MESSAGE_HOST", "graph.facebook.com")
IG_USER_ID = get_secret("IG_USER_ID")
IG_ACCESS_TOKEN = get_secret("IG_ACCESS_TOKEN")
IG_SHARE_TO_FEED = get_secret("IG_SHARE_TO_FEED", "false").lower() == "true"
IG_CONTAINER_POLL_INTERVAL = int(get_secret("IG_CONTAINER_POLL_INTERVAL", "3") or 3)
IG_CONTAINER_POLL_TIMEOUT = int(get_secret("IG_CONTAINER_POLL_TIMEOUT", "120") or 120)
IG_MINING_ENABLED = get_secret("IG_MINING_ENABLED", "true").lower() == "true"
IG_MINING_TOP_MEDIA = get_secret("IG_MINING_TOP_MEDIA", "true").lower() == "true"
IG_MINING_MAX_RESULTS = int(get_secret("IG_MINING_MAX_RESULTS", "6") or 6)

# 영상 공개 URL 생성(그래프 API 업로드용)
VIDEO_HOSTING = get_secret("VIDEO_HOSTING", "cloudinary")  # cloudinary | public_url | none
VIDEO_PUBLIC_BASE_URL = get_secret("VIDEO_PUBLIC_BASE_URL")  # public_url 모드일 때 사용
CLOUDINARY_CLOUD_NAME = get_secret("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = get_secret("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = get_secret("CLOUDINARY_API_SECRET")
CLOUDINARY_FOLDER = get_secret("CLOUDINARY_FOLDER", "sns-marketing-bot")

COUPANG_ACCESS_KEY = get_secret("COUPANG_ACCESS_KEY")
COUPANG_SECRET_KEY = get_secret("COUPANG_SECRET_KEY")
COUPANG_PARTNER_ID = get_secret("COUPANG_PARTNER_ID")

ALIEXPRESS_APP_KEY = get_secret("ALIEXPRESS_APP_KEY")
ALIEXPRESS_APP_SECRET = get_secret("ALIEXPRESS_APP_SECRET")
ALIEXPRESS_TRACKING_ID = get_secret("ALIEXPRESS_TRACKING_ID")
ALIEXPRESS_LANGUAGE = get_secret("ALIEXPRESS_LANGUAGE", "EN")
ALIEXPRESS_CURRENCY = get_secret("ALIEXPRESS_CURRENCY", "USD")
ALIEXPRESS_DEFAULT_KEYWORD = get_secret("ALIEXPRESS_DEFAULT_KEYWORD", "")

# AliExpress 생활용품 키워드 풀 (자동 소싱용)
ALIEXPRESS_KEYWORD_POOL = _split_csv(
    get_secret(
        "ALIEXPRESS_KEYWORD_POOL",
        "kitchen organizer,drawer organizer,storage box,under sink organizer,"
        "dish drying rack,silicone baking mat,food storage container,"
        "bathroom shelf,shower caddy,soap dispenser,toothbrush holder,"
        "microfiber cloth,cleaning brush,lint remover,mop,trash bin,"
        "cable organizer,power strip,travel bottle,lunch box,water bottle"
    )
)

# AliExpress 검색 결과 제외 키워드 (의류 등)
ALIEXPRESS_EXCLUDE_KEYWORDS = _split_csv(
    get_secret(
        "ALIEXPRESS_EXCLUDE_KEYWORDS",
        "dress,top,shirt,t-shirt,tee,blouse,hoodie,sweater,cardigan,jacket,coat,"
        "pants,jeans,leggings,skirt,shorts,bra,underwear,lingerie,pajama,onesie,"
        "원피스,상의,하의,셔츠,티셔츠,후드,니트,가디건,자켓,코트,바지,팬츠,레깅스,치마,잠옷,속옷"
    )
)

# 브랜드/모델명 키워드 확장 (예: '샴푸' -> 특정 브랜드/모델명)
BRAND_MODEL_ENRICH = get_secret("BRAND_MODEL_ENRICH", "true").lower() == "true"
BRAND_MODEL_CACHE_DAYS = int(get_secret("BRAND_MODEL_CACHE_DAYS", "7") or 7)
GENERIC_KEYWORDS = _split_csv(
    get_secret(
        "GENERIC_KEYWORDS",
        "샴푸"
    )
)

# ──────────────────────────────────────────────
# 크롤링 / 필터 설정
# ──────────────────────────────────────────────
COUPANG_GOLDBOX_URL = "https://www.coupang.com/np/goldbox"
COUPANG_RANKING_URL = "https://www.coupang.com/np/categories/393760"

# 바이럴 영상 필터 기준 (완화 기본값)
MIN_VIEW_COUNT = int(get_secret("MIN_VIEW_COUNT", "10000") or 10000)  # 최소 조회수
MIN_LIKE_COUNT = int(get_secret("MIN_LIKE_COUNT", "200") or 200)      # 최소 좋아요
MIN_DURATION = int(get_secret("MIN_DURATION", "5") or 5)             # 최소 영상 길이 (초)
MAX_DURATION = int(get_secret("MAX_DURATION", "90") or 90)            # 최대 영상 길이 (초)

# yt-dlp 다운로드 옵션
YTDLP_USER_AGENT = get_secret(
    "YTDLP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
YTDLP_REFERER = get_secret("YTDLP_REFERER", "https://www.youtube.com/")
YTDLP_EXTRACTOR_ARGS = get_secret("YTDLP_EXTRACTOR_ARGS", "youtube:player_client=android")
YTDLP_COOKIES_FILE = get_secret("YTDLP_COOKIES_FILE", "")
YTDLP_RETRIES = int(get_secret("YTDLP_RETRIES", "3") or 3)
YTDLP_SLEEP_INTERVAL = int(get_secret("YTDLP_SLEEP_INTERVAL", "1") or 1)
YTDLP_MAX_SLEEP_INTERVAL = int(get_secret("YTDLP_MAX_SLEEP_INTERVAL", "5") or 5)

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
ALIEXPRESS_VIDEO_FIRST = get_secret("ALIEXPRESS_VIDEO_FIRST", "false").lower() == "true"
VIDEO_FIRST_MIN_VIDEOS = int(get_secret("VIDEO_FIRST_MIN_VIDEOS", "4") or 4)
VIDEO_FIRST_MAX_VIDEOS = int(get_secret("VIDEO_FIRST_MAX_VIDEOS", "5") or 5)
PRODUCT_FIRST_MIN_VIDEOS = int(get_secret("PRODUCT_FIRST_MIN_VIDEOS", "1") or 1)
PRODUCT_FIRST_MAX_CANDIDATES = int(get_secret("PRODUCT_FIRST_MAX_CANDIDATES", "10") or 10)

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

HOOK_TEXT_PROMPT = """다음 제품에 대한 한국어 '불편 해결' 후킹 문구를 1개 만들어주세요.
조건:
- 12자 이내
- 일상 불편을 딱 짚기
- 느낌표 1개
- 이모지 1개
- 과장/허세 표현 최소화
제품명: {product_name}
"""

CAPTION_PROMPT = """다음 제품에 대한 인스타그램 릴스 본문을 작성해주세요.
스타일: 생활꿀템/집꾸미기 계정 톤, 짧고 공감형
구성:
1) 불편/문제 공감 1문장
2) 해결/제품 장점 1문장
3) 댓글 CTA + DM 안내 1문장 (댓글 키워드: {cta_keyword})
4) 프로필 링크에서 {product_code} 검색 안내 1문장 (product_code 없으면 생략)
조건:
- 2~4줄
- 해시태그 8~12개
- 과장 광고 문구 금지
제품명: {product_name}
"""

REPLY_TEMPLATES = [
    "요청하신 정보 DM으로 보내드렸어요! 💌",
    "DM 확인해주세요! 😊",
    "정보 DM으로 전달했습니다!",
    "DM 보냈어요~ 확인해주세요!",
    "바로 DM 보내드렸습니다!",
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
