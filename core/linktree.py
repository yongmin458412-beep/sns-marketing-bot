"""
linktree.py - Linktree/Webhook 연동 모듈
공식 API가 없거나 접근 권한이 없을 경우를 대비해
Webhook 또는 큐 파일 방식으로 링크 업로드를 지원합니다.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from config import (
    DATA_DIR,
    LINKTREE_MODE,
    LINKTREE_WEBHOOK_URL,
    LINKTREE_WEBHOOK_SECRET,
)

logger = logging.getLogger(__name__)


class LinktreeManager:
    """Linktree 업로드 관리 (webhook/queue)"""

    def __init__(self):
        self.mode = (LINKTREE_MODE or "webhook").lower()
        self.webhook_url = LINKTREE_WEBHOOK_URL
        self.webhook_secret = LINKTREE_WEBHOOK_SECRET
        self.queue_file = DATA_DIR / "linktree_queue.jsonl"

    def is_ready(self) -> bool:
        if self.mode == "disabled":
            return False
        if self.mode == "webhook":
            return bool(self.webhook_url)
        return True

    def publish_link(self, product_name: str, product_code: str,
                     url: str, source: str = "") -> str:
        """
        Linktree에 링크 등록 (webhook/queue)
        Returns: 등록된 링크 URL (알 수 없으면 빈 문자열)
        """
        title = self._build_title(product_name, product_code)
        payload = {
            "title": title,
            "url": url,
            "product_name": product_name,
            "product_code": product_code,
            "source": source,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        if self.mode == "disabled":
            logger.info("Linktree 모드가 disabled 입니다. 업로드 스킵")
            return ""

        if self.mode == "webhook":
            return self._send_webhook(payload)

        # queue 모드: 파일에 적재 (수동 업로드용)
        try:
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.queue_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            logger.info(f"Linktree 큐 적재 완료: {title}")
        except Exception as e:
            logger.error(f"Linktree 큐 적재 실패: {e}")

        return ""

    def _send_webhook(self, payload: dict) -> str:
        if not self.webhook_url:
            logger.error("LINKTREE_WEBHOOK_URL이 설정되지 않았습니다.")
            return ""

        headers = {"Content-Type": "application/json"}
        if self.webhook_secret:
            headers["X-Linktree-Secret"] = self.webhook_secret

        try:
            resp = requests.post(
                self.webhook_url,
                headers=headers,
                json=payload,
                timeout=10
            )
            if resp.status_code >= 200 and resp.status_code < 300:
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                link_url = data.get("link_url") or data.get("url") or ""
                logger.info(f"Linktree webhook 성공: {link_url or 'ok'}")
                return link_url

            logger.error(f"Linktree webhook 실패: {resp.status_code} {resp.text[:200]}")
            return ""
        except Exception as e:
            logger.error(f"Linktree webhook 호출 실패: {e}")
            return ""

    @staticmethod
    def _build_title(product_name: str, product_code: str) -> str:
        name = (product_name or "").strip()
        code = (product_code or "").strip()
        if code and name:
            return f"{code} | {name}"
        return code or name or "Product"

