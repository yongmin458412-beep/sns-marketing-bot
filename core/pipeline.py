"""
pipeline.py - 전체 자동화 파이프라인 통합 모듈
소싱 → 마이닝 → 편집 → 업로드 → 댓글 처리의 전체 흐름을 관리합니다.
"""

import logging
import asyncio
import traceback
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    MAX_PRODUCTS_PER_RUN, COMMENT_POLL_INTERVAL,
    MAX_DAILY_PRODUCTS, ALIEXPRESS_DEFAULT_KEYWORD,
    ALIEXPRESS_KEYWORD_POOL,
)
from core.sourcing import ProductSourcer
from core.mining import VideoMiner
from core.editing import VideoEditor
from core.social import InstagramManager
from core.bot import TelegramNotifier
from core.linktree import LinktreeManager
from core.notion_links import NotionLinkManager
from core.trends import get_daily_trend_keyword, pick_daily_from_pool
from core.database import (
    start_run_log, finish_run_log,
    get_today_product_count,
    update_product_affiliate_link,
    update_product_linktree,
    update_product_notion,
)

logger = logging.getLogger(__name__)


class AutomationPipeline:
    """전체 자동화 파이프라인 관리 클래스"""

    def __init__(self):
        self.sourcer = ProductSourcer()
        self.miner = VideoMiner()
        self.editor = VideoEditor()
        self.social = InstagramManager()
        self.notifier = TelegramNotifier()
        self.linktree = LinktreeManager()
        self.notion = NotionLinkManager()

    async def run_full_pipeline(self, source_url: str = None,
                                max_products: int = None,
                                monitor_comments: bool = True,
                                monitor_duration: int = 30,
                                source: str = "aliexpress",
                                keyword: str = None) -> dict:
        """
        전체 자동화 파이프라인 실행

        Args:
            source_url: 쿠팡 크롤링 URL (기본: 골드박스)
            max_products: 처리할 최대 상품 수
            monitor_comments: 댓글 모니터링 활성화 여부
            monitor_duration: 댓글 모니터링 시간 (분)

        Returns: 실행 결과 통계
        """
        max_products = max_products or MAX_PRODUCTS_PER_RUN
        run_id = start_run_log(run_type="auto")
        stats = {
            "products": 0, "videos": 0,
            "posts": 0, "dms": 0, "errors": []
        }

        try:
            self.notifier.notify_start()
            logger.info("=" * 60)
            logger.info("전체 자동화 파이프라인 시작")
            logger.info("=" * 60)

            # ── DAILY LIMIT ──
            today_count = get_today_product_count()
            remaining = max(0, MAX_DAILY_PRODUCTS - today_count)
            if remaining <= 0:
                msg = f"하루 최대 상품 수({MAX_DAILY_PRODUCTS})를 초과하여 종료합니다."
                logger.warning(msg)
                stats["errors"].append(msg)
                finish_run_log(run_id, status="failed", error=msg)
                return stats

            if max_products > remaining:
                max_products = remaining

            # ── STEP 1: 상품 소싱 ──
            logger.info("\n[STEP 1/5] 상품 소싱 중...")
            if source == "aliexpress" and not keyword:
                keyword = (
                    pick_daily_from_pool(ALIEXPRESS_KEYWORD_POOL)
                    or get_daily_trend_keyword()
                    or ALIEXPRESS_DEFAULT_KEYWORD
                )
            if source == "aliexpress" and not keyword:
                raise Exception("AliExpress 소싱 키워드가 설정되지 않았습니다.")

            products = await self.sourcer.run_sourcing_pipeline(
                url=source_url,
                source=source,
                keyword=keyword,
                max_items=max_products
            )
            products = products[:max_products]
            stats["products"] = len(products)

            if not products:
                raise Exception("소싱된 상품이 없습니다.")

            for p in products:
                self.notifier.notify_product_sourced(
                    p.get("name", "Unknown"),
                    p.get("keywords", [])
                )

            # ── STEP 2~4: 각 상품별 처리 ──
            for idx, product in enumerate(products, 1):
                product_name = product.get("name_en") or product.get("name", "Unknown")
                logger.info(f"\n{'='*40}")
                logger.info(f"상품 {idx}/{len(products)}: {product_name}")
                logger.info(f"{'='*40}")

                try:
                    # STEP 2: 영상 마이닝
                    logger.info(f"[STEP 2/5] 영상 마이닝: {product_name}")
                    videos = self.miner.run_mining_pipeline(product)

                    if not videos:
                        logger.warning(f"영상을 찾지 못했습니다: {product_name}")
                        continue

                    # STEP 3: 영상 편집
                    logger.info(f"[STEP 3/5] 영상 편집: {product_name}")
                    edited_videos = self.editor.batch_edit(
                        videos, product_name=product_name
                    )
                    stats["videos"] += len(edited_videos)

                    self.notifier.notify_video_created(
                        product_name, len(edited_videos)
                    )

                    if not edited_videos:
                        logger.warning(f"편집된 영상이 없습니다: {product_name}")
                        continue

                    # STEP 4: 인스타그램 업로드
                    logger.info(f"[STEP 4/5] 인스타그램 업로드: {product_name}")

                    # 제휴 링크 생성
                    affiliate_link = (
                        product.get("affiliate_link")
                        or InstagramManager.generate_affiliate_link(product.get("link", ""))
                    )
                    if affiliate_link:
                        update_product_affiliate_link(product.get("id"), affiliate_link)

                    # Notion 업로드 (우선) / Linktree (옵션)
                    bio_url = ""
                    if self.notion.is_ready():
                        notion_url = self.notion.upsert_product(
                            product_code=product.get("product_code", ""),
                            product_name=product_name,
                            link_url=affiliate_link or product.get("link", ""),
                            source=product.get("source", source),
                            price=product.get("price", ""),
                            image_url=product.get("image_url", "")
                        )
                        if notion_url:
                            update_product_notion(product.get("id"), notion_url)
                            product["notion_url"] = notion_url
                        bio_url = self.notion.get_public_url() or notion_url

                    if not bio_url and self.linktree.is_ready():
                        linktree_url = self.linktree.publish_link(
                            product_name=product_name,
                            product_code=product.get("product_code", ""),
                            url=affiliate_link or product.get("link", ""),
                            source=product.get("source", source)
                        )
                        if linktree_url:
                            update_product_linktree(product.get("id"), linktree_url)
                            product["linktree_url"] = linktree_url
                            bio_url = linktree_url

                    for video in edited_videos:
                        media_id = self.social.upload_reel(
                            video_path=video["edited_path"],
                            product_name=product_name,
                            product_id=product.get("id"),
                            video_id=video.get("id"),
                            product_code=product.get("product_code", "")
                        )

                        if media_id:
                            stats["posts"] += 1
                            self.notifier.notify_upload_success(
                                product_name, media_id
                            )

                            # STEP 5: 댓글 모니터링
                            if monitor_comments:
                                logger.info(
                                    f"[STEP 5/5] 댓글 모니터링: "
                                    f"{product_name} ({monitor_duration}분)"
                                )
                                engagement = self.social.monitor_comments(
                                    media_id=media_id,
                                    product_name=product_name,
                                    product_code=product.get("product_code", ""),
                                    affiliate_link=affiliate_link,
                                    bio_url=bio_url,
                                    duration_minutes=monitor_duration
                                )
                                stats["dms"] += engagement.get("dms", 0)

                                self.notifier.notify_engagement(
                                    product_name,
                                    engagement.get("replies", 0),
                                    engagement.get("dms", 0)
                                )

                except Exception as e:
                    error_msg = f"상품 처리 실패 [{product_name}]: {e}"
                    logger.error(error_msg)
                    stats["errors"].append(error_msg)
                    self.notifier.notify_error(error_msg)
                    continue

            # 완료
            self.notifier.notify_complete(stats)
            finish_run_log(
                run_id,
                products=stats["products"],
                videos=stats["videos"],
                posts=stats["posts"],
                dms=stats["dms"],
                status="completed"
            )

        except Exception as e:
            error_msg = f"파이프라인 오류: {traceback.format_exc()}"
            logger.error(error_msg)
            self.notifier.notify_error(str(e))
            finish_run_log(run_id, status="failed", error=str(e))
            stats["errors"].append(str(e))

        logger.info("\n" + "=" * 60)
        logger.info("파이프라인 실행 완료")
        logger.info(f"결과: {stats}")
        logger.info("=" * 60)

        return stats

    async def run_sourcing_only(self, url: str = None,
                                source: str = "aliexpress",
                                keyword: str = None,
                                max_products: int = None) -> list:
        """소싱만 실행"""
        max_products = max_products or MAX_PRODUCTS_PER_RUN
        today_count = get_today_product_count()
        remaining = max(0, MAX_DAILY_PRODUCTS - today_count)
        if remaining <= 0:
            logger.warning(f"하루 최대 상품 수({MAX_DAILY_PRODUCTS})를 초과하여 소싱을 중단합니다.")
            return []
        if max_products > remaining:
            max_products = remaining

        if source == "aliexpress" and not keyword:
            keyword = (
                pick_daily_from_pool(ALIEXPRESS_KEYWORD_POOL)
                or get_daily_trend_keyword()
                or ALIEXPRESS_DEFAULT_KEYWORD
            )
        return await self.sourcer.run_sourcing_pipeline(
            url=url,
            source=source,
            keyword=keyword,
            max_items=max_products
        )

    def run_mining_only(self, product: dict) -> list:
        """마이닝만 실행"""
        return self.miner.run_mining_pipeline(product)

    def run_editing_only(self, video_path: str,
                         product_name: str = "") -> str:
        """편집만 실행"""
        return self.editor.edit_video(video_path, product_name)

    def run_upload_only(self, video_path: str,
                        product_name: str = "") -> str:
        """업로드만 실행"""
        return self.social.upload_reel(video_path, product_name)


# ──────────────────────────────────────────────
# CLI 실행 (GitHub Actions에서 호출)
# ──────────────────────────────────────────────
async def main():
    """메인 실행 함수"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    pipeline = AutomationPipeline()
    result = await pipeline.run_full_pipeline(
        monitor_comments=True,
        monitor_duration=30,
        source="aliexpress",
        keyword=None
    )

    print(f"\n최종 결과: {result}")
    return result


if __name__ == "__main__":
    asyncio.run(main())
