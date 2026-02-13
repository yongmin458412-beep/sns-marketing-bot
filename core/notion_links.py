"""
notion_links.py - Notion 링크 페이지 자동 업데이트
"""

import logging
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    NOTION_TOKEN,
    NOTION_DATABASE_ID,
    NOTION_PUBLIC_URL,
    NOTION_PROP_NAME,
    NOTION_PROP_CODE,
    NOTION_PROP_LINK,
    NOTION_PROP_SOURCE,
    NOTION_PROP_PRICE,
    NOTION_PROP_IMAGE,
)

logger = logging.getLogger(__name__)


class NotionLinkManager:
    """Notion 데이터베이스에 상품 링크를 업서트"""

    def __init__(self):
        self.client = None
        self.database_id = NOTION_DATABASE_ID

        if not NOTION_TOKEN or not NOTION_DATABASE_ID:
            logger.warning("NOTION_TOKEN 또는 NOTION_DATABASE_ID가 설정되지 않았습니다.")
            return

        try:
            from notion_client import Client  # type: ignore
            self.client = Client(auth=NOTION_TOKEN)
        except Exception as e:
            logger.error(f"notion-client 초기화 실패: {e}")
            self.client = None

    def is_ready(self) -> bool:
        return self.client is not None

    def get_public_url(self) -> str:
        return NOTION_PUBLIC_URL or ""

    def upsert_product(self, product_code: str, product_name: str,
                       link_url: str, source: str = "",
                       price: str = "", image_url: str = "") -> str:
        """
        제품번호 기준으로 Notion DB에 업서트
        Returns: Notion page url (가능한 경우)
        """
        if not self.client or not self.database_id:
            return ""

        try:
            page_id = self._find_page_id_by_code(product_code)
            props = self._build_properties(
                product_code, product_name, link_url, source, price, image_url
            )

            if page_id:
                page = self.client.pages.update(page_id=page_id, properties=props)
            else:
                page = self.client.pages.create(
                    parent={"database_id": self.database_id},
                    properties=props
                )
            return page.get("url", "") if isinstance(page, dict) else ""

        except Exception as e:
            logger.error(f"Notion 업서트 실패: {e}")
            return ""

    def _find_page_id_by_code(self, product_code: str) -> Optional[str]:
        if not product_code:
            return None

        # 1) rich_text 속성으로 조회
        try:
            resp = self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "property": NOTION_PROP_CODE,
                    "rich_text": {"equals": product_code}
                },
                page_size=1
            )
            results = resp.get("results", [])
            if results:
                return results[0].get("id")
        except Exception:
            pass

        # 2) title 속성으로 조회 (fallback)
        try:
            resp = self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "property": NOTION_PROP_NAME,
                    "title": {"contains": product_code}
                },
                page_size=1
            )
            results = resp.get("results", [])
            if results:
                return results[0].get("id")
        except Exception:
            return None

        return None

    def _build_properties(self, product_code: str, product_name: str,
                          link_url: str, source: str,
                          price: str, image_url: str) -> dict:
        props = {
            NOTION_PROP_NAME: {
                "title": [{"text": {"content": self._build_title(product_code, product_name)}}]
            },
            NOTION_PROP_CODE: {
                "rich_text": [{"text": {"content": product_code}}]
            },
        }

        if link_url:
            props[NOTION_PROP_LINK] = {"url": link_url}
        if source:
            props[NOTION_PROP_SOURCE] = {"select": {"name": source}}
        if price:
            props[NOTION_PROP_PRICE] = {"rich_text": [{"text": {"content": str(price)}}]}
        if image_url:
            props[NOTION_PROP_IMAGE] = {"url": image_url}

        return props

    @staticmethod
    def _build_title(product_code: str, product_name: str) -> str:
        code = (product_code or "").strip()
        name = (product_name or "").strip()
        if code and name:
            return f"{code} | {name}"
        return code or name or "Product"

