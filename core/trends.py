"""
trends.py - 한국 트렌드 키워드 수집
Google Trends 기반으로 오늘의 키워드를 가져오고 캐시합니다.
"""

import json
import logging
import random
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    DATA_DIR,
    TREND_SOURCE,
    TREND_GEO,
    TREND_MAX_ITEMS,
    TREND_FALLBACK_KEYWORDS,
    ALIEXPRESS_DEFAULT_KEYWORD,
)

logger = logging.getLogger(__name__)


def get_daily_trend_keyword() -> str:
    """오늘 날짜 기준으로 트렌드 키워드 1개 반환"""
    keywords = get_daily_trend_keywords()
    if not keywords:
        return ALIEXPRESS_DEFAULT_KEYWORD

    today = datetime.now().strftime("%Y-%m-%d")
    rng = random.Random(today)  # 날짜 기반 랜덤 (하루 내 고정)
    return rng.choice(keywords)


def pick_daily_from_pool(pool: list[str]) -> str:
    """고정된 키워드 풀에서 날짜 기준으로 1개 선택"""
    pool = [p.strip() for p in pool if p and p.strip()]
    if not pool:
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    rng = random.Random(today + "|pool")
    return rng.choice(pool)


def get_daily_trend_keywords() -> list[str]:
    """오늘 날짜 기준 트렌드 키워드 리스트 반환 (캐시 포함)"""
    today = datetime.now().strftime("%Y-%m-%d")
    cache_path = DATA_DIR / "trend_keywords_cache.json"

    cached = _load_cache(cache_path)
    if cached.get("date") == today and cached.get("keywords"):
        return cached["keywords"]

    keywords = []

    if (TREND_SOURCE or "").lower() == "google_trends":
        keywords = _fetch_google_trends()

    if not keywords:
        keywords = list(TREND_FALLBACK_KEYWORDS)

    if not keywords and ALIEXPRESS_DEFAULT_KEYWORD:
        keywords = [ALIEXPRESS_DEFAULT_KEYWORD]

    _save_cache(cache_path, today, keywords)
    return keywords


def _fetch_google_trends() -> list[str]:
    """Google Trends에서 한국 트렌드 키워드 가져오기 (최대 TREND_MAX_ITEMS)"""
    try:
        from pytrends.request import TrendReq  # type: ignore
    except Exception as e:
        logger.warning(f"pytrends 불러오기 실패: {e}")
        return []

    try:
        pytrends = TrendReq(hl="ko-KR", tz=540)

        # 여러 옵션 시도 (환경별 지원 차이 대비)
        candidates = []
        for pn in (TREND_GEO, "KR", "south_korea", "korea"):
            try:
                df = pytrends.trending_searches(pn=pn)
                if df is not None and len(df) > 0:
                    candidates = df.iloc[:, 0].astype(str).tolist()
                    break
            except Exception:
                continue

        if not candidates:
            try:
                df = pytrends.today_searches(pn=TREND_GEO)
                if df is not None and len(df) > 0:
                    candidates = df.iloc[:, 0].astype(str).tolist()
            except Exception:
                candidates = []

        keywords = [k.strip() for k in candidates if k and k.strip()]
        keywords = keywords[:TREND_MAX_ITEMS]
        logger.info(f"Google Trends 키워드 {len(keywords)}개 수집")
        return keywords
    except Exception as e:
        logger.warning(f"Google Trends 수집 실패: {e}")
        return []


def _load_cache(path: Path) -> dict:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cache(path: Path, date_str: str, keywords: list[str]):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": date_str, "keywords": keywords}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"트렌드 캐시 저장 실패: {e}")
