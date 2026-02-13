"""
aliexpress_api.py - AliExpress Open Platform 연동 모듈
python-aliexpress-api 라이브러리를 사용합니다.
"""

import logging
from typing import Any

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    ALIEXPRESS_APP_KEY,
    ALIEXPRESS_APP_SECRET,
    ALIEXPRESS_TRACKING_ID,
    ALIEXPRESS_LANGUAGE,
    ALIEXPRESS_CURRENCY,
)

logger = logging.getLogger(__name__)


class AliExpressClient:
    """AliExpress API 래퍼 (상품 검색 + 제휴링크 생성)"""

    def __init__(self):
        self.api = None
        self.models = None

        if not all([ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID]):
            logger.warning("AliExpress API 키/시크릿/Tracking ID가 설정되지 않았습니다.")
            return

        try:
            from aliexpress_api import AliexpressApi, models  # type: ignore
        except ImportError:
            logger.error("python-aliexpress-api가 설치되지 않았습니다. pip install python-aliexpress-api")
            return

        # 언어/통화 설정 (기본 EN/USD)
        lang = getattr(models.Language, ALIEXPRESS_LANGUAGE, models.Language.EN)
        currency = getattr(models.Currency, ALIEXPRESS_CURRENCY, models.Currency.USD)

        try:
            self.api = AliexpressApi(
                ALIEXPRESS_APP_KEY,
                ALIEXPRESS_APP_SECRET,
                lang,
                currency,
                ALIEXPRESS_TRACKING_ID,
            )
            self.models = models
            logger.info("AliExpress API 클라이언트 초기화 완료")
        except Exception as e:
            logger.error(f"AliExpress API 초기화 실패: {e}")
            self.api = None

    def is_ready(self) -> bool:
        return self.api is not None

    def search_products(self, keyword: str, max_items: int = 10) -> list[dict]:
        """키워드로 상품 검색"""
        if not keyword:
            return []
        if not self.api:
            logger.error("AliExpress API 미설정 - 검색 불가")
            return []

        try:
            result = self.api.get_products(keywords=keyword)
        except Exception as e:
            logger.error(f"AliExpress 상품 검색 실패: {e}")
            return []

        products = getattr(result, "products", None) or getattr(result, "data", None) or result
        if products is None:
            return []
        if not isinstance(products, list):
            try:
                products = list(products)
            except Exception:
                products = [products]

        normalized = []
        for p in products[:max_items]:
            item = self._normalize_product(p)
            if not item.get("name"):
                continue

            # 제휴 링크 생성 시도
            if item.get("link"):
                item["affiliate_link"] = self.get_affiliate_link(item["link"])
            item["source"] = "aliexpress"
            normalized.append(item)

        logger.info(f"AliExpress 상품 {len(normalized)}개 수집 완료")
        return normalized

    def get_affiliate_link(self, product_url: str) -> str:
        """AliExpress 제휴 링크 생성 (실패 시 원본 URL 반환)"""
        if not self.api or not product_url:
            return product_url

        try:
            links = self.api.get_affiliate_links(product_url)
        except Exception as e:
            logger.warning(f"AliExpress 제휴 링크 생성 실패: {e}")
            return product_url

        link_list = getattr(links, "affiliate_links", None) or getattr(links, "links", None) or links
        if isinstance(link_list, list) and link_list:
            promo = self._get_attr(
                link_list[0],
                "promotion_link",
                "promotion_link_url",
                "promotion_link",
                "url",
            )
            if promo:
                return str(promo)

        return product_url

    def _normalize_product(self, product: Any) -> dict:
        title = self._get_attr(product, "product_title", "title", "product_name", "name")
        image_url = self._get_attr(
            product,
            "product_main_image_url",
            "image_url",
            "product_image_url",
            "main_image_url",
        )
        price = self._get_attr(
            product,
            "target_sale_price",
            "sale_price",
            "app_sale_price",
            "product_price",
            "price",
        )
        link = self._get_attr(
            product,
            "product_detail_url",
            "detail_url",
            "product_url",
            "url",
        )

        return {
            "name": str(title) if title else "",
            "image_url": str(image_url) if image_url else "",
            "price": str(price) if price else "",
            "link": str(link) if link else "",
        }

    @staticmethod
    def _get_attr(obj: Any, *names: str) -> Any:
        for name in names:
            if isinstance(obj, dict) and name in obj and obj[name]:
                return obj[name]
            if hasattr(obj, name):
                val = getattr(obj, name)
                if val:
                    return val
        return ""

