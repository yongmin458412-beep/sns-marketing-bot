"""
pipeline.py - 전체 자동화 파이프라인 통합 모듈
소싱 → 마이닝 → 편집 → 업로드 → 댓글 처리의 전체 흐름을 관리합니다.
"""

import logging
import asyncio
import traceback
import random
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    MAX_PRODUCTS_PER_RUN, COMMENT_POLL_INTERVAL,
    MAX_DAILY_PRODUCTS, ALIEXPRESS_DEFAULT_KEYWORD,
    ALIEXPRESS_KEYWORD_POOL, LIFESTYLE_KEYWORD_POOL,
    ALIEXPRESS_VIDEO_FIRST, VIDEO_FIRST_MIN_VIDEOS, VIDEO_FIRST_MAX_VIDEOS,
    TREND_FALLBACK_KEYWORDS,
    BRAND_MODEL_ENRICH,
    PRODUCT_FIRST_MIN_VIDEOS, PRODUCT_FIRST_MAX_CANDIDATES,
    DAILY_TWO_MODE, DAILY_TWO_MAX_VIDEOS_PER_PRODUCT,
)
from core.sourcing import ProductSourcer
from core.mining import VideoMiner
from core.editing import VideoEditor
from core.social import InstagramManager
from core.bot import TelegramNotifier
from core.linktree import LinktreeManager
from core.notion_links import NotionLinkManager
from core.trends import (
    get_daily_trend_keyword, pick_daily_from_pool, get_daily_trend_keywords,
    pick_daily_from_pool_key, get_daily_seasonal_keyword, get_seasonal_keyword_pool
)
from core.database import (
    start_run_log, finish_run_log,
    get_today_product_count,
    insert_product, update_product_code,
    update_product_affiliate_link,
    update_product_linktree,
    update_product_notion,
    update_video_product,
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

            # ── DAILY 2 VIDEOS MODE ──
            if source == "aliexpress" and DAILY_TWO_MODE and not keyword:
                logger.info("\n[MODE] 하루 2개(생활꿀템 + 계절용품) 모드")
                return await self._run_daily_two_categories(
                    run_id=run_id,
                    stats=stats,
                    monitor_comments=monitor_comments,
                    monitor_duration=monitor_duration,
                    source=source,
                )

            # ── STEP 1: 소싱 or Video-First ──
            if source == "aliexpress" and ALIEXPRESS_VIDEO_FIRST:
                logger.info("\n[STEP 1/5] Video-First 모드 시작...")
                return await self._run_video_first_internal(
                    run_id=run_id,
                    stats=stats,
                    max_products=max_products,
                    monitor_comments=monitor_comments,
                    monitor_duration=monitor_duration,
                    keyword=keyword,
                    source=source,
                )

            if source == "aliexpress":
                logger.info("\n[STEP 1/5] Product-First(영상 필수) 모드 시작...")
                return await self._run_product_first_video_required(
                    run_id=run_id,
                    stats=stats,
                    max_products=max_products,
                    monitor_comments=monitor_comments,
                    monitor_duration=monitor_duration,
                    keyword=keyword,
                    source=source,
                )

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
                    cta_keyword = product.get("cta_keyword", "")

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
                            product_code=product.get("product_code", ""),
                            cta_keyword=cta_keyword
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
                                    cta_keyword=cta_keyword,
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

    async def _run_video_first_internal(self, run_id: int, stats: dict,
                                        max_products: int,
                                        monitor_comments: bool,
                                        monitor_duration: int,
                                        keyword: str | None,
                                        source: str = "aliexpress") -> dict:
        """
        Video-First: 영상 수집 → 상품 검색(AliExpress) → 편집/업로드
        """
        keyword_stream = self._video_first_keyword_stream(keyword)
        first_keyword = None
        try:
            first_keyword = next(keyword_stream)
        except Exception:
            first_keyword = None

        if not first_keyword:
            msg = "Video-First 키워드가 없습니다."
            logger.error(msg)
            stats["errors"].append(msg)
            finish_run_log(run_id, status="failed", error=msg)
            return stats

        # 하루 제한
        today_count = get_today_product_count()
        remaining = max(0, MAX_DAILY_PRODUCTS - today_count)
        if remaining <= 0:
            msg = f"하루 최대 상품 수({MAX_DAILY_PRODUCTS})를 초과하여 종료합니다."
            logger.warning(msg)
            stats["errors"].append(msg)
            finish_run_log(run_id, status="failed", error=msg)
            return stats

        max_products = min(max_products, remaining)

        # 제품 후보 최대 max_products 개 처리 (무한 루프)
        while stats["products"] < max_products:
            try:
                kw = first_keyword if first_keyword else next(keyword_stream)
                first_keyword = None
            except Exception:
                kw = None

            if not kw:
                continue

            try:
                logger.info(f"\n[STEP 1/5] 영상 수집 키워드: {kw}")
                videos = self.miner.mine_by_keyword(
                    kw, max_videos=VIDEO_FIRST_MAX_VIDEOS
                )
                if len(videos) < VIDEO_FIRST_MIN_VIDEOS:
                    logger.warning(
                        f"영상 부족({len(videos)}/{VIDEO_FIRST_MIN_VIDEOS}) - 키워드 스킵: {kw}"
                    )
                    continue

                stats["videos"] += len(videos)

                # 영상 타이틀 기반으로 검색어 추정
                product_query = self.miner.infer_product_query(videos, fallback=kw) or kw
                logger.info(f"[STEP 2/5] AliExpress 상품 검색: {product_query}")
                products = self.sourcer.search_aliexpress_products(
                    keyword=product_query,
                    max_items=1
                )
                if not products:
                    logger.warning(f"AliExpress 상품 없음: {product_query}")
                    continue

                # 상품 분석/저장
                product = products[0]
                analysis = self.sourcer.analyze_product_by_name(product.get("name", ""))
                product["name_en"] = analysis.get("product_name", "")
                product["keywords"] = analysis.get("keywords", [])
                product["cta_keyword"] = self.sourcer.infer_cta_keyword(
                    product.get("name_en") or product.get("name", ""),
                    product.get("keywords", [])
                )

                product_id = insert_product(
                    name=product["name"],
                    name_en=product["name_en"],
                    keywords=product["keywords"],
                    image_url=product.get("image_url", ""),
                    price=product.get("price", ""),
                    affiliate_link=product.get("affiliate_link", product.get("link", "")),
                    source=product.get("source", source),
                    cta_keyword=product.get("cta_keyword", "")
                )
                product_code = self.sourcer._generate_product_code(
                    product_id,
                    product.get("source", source)
                )
                update_product_code(product_id, product_code)
                product["id"] = product_id
                product["product_code"] = product_code

                stats["products"] += 1

                # 영상에 product_id 연결
                for v in videos:
                    vid = v.get("id")
                    if vid:
                        update_video_product(vid, product_id)

                # ── STEP 3: 영상 편집 ──
                logger.info(f"[STEP 3/5] 영상 편집: {product.get('name_en')}")
                edited_videos = self.editor.batch_edit(
                    videos, product_name=product.get("name_en") or product.get("name", "")
                )
                if not edited_videos:
                    logger.warning("편집된 영상 없음")
                    continue

                # ── STEP 4: 업로드 ──
                affiliate_link = (
                    product.get("affiliate_link")
                    or InstagramManager.generate_affiliate_link(product.get("link", ""))
                )
                if affiliate_link:
                    update_product_affiliate_link(product_id, affiliate_link)

                bio_url = ""
                if self.notion.is_ready():
                    notion_url = self.notion.upsert_product(
                        product_code=product.get("product_code", ""),
                        product_name=product.get("name_en") or product.get("name", ""),
                        link_url=affiliate_link or product.get("link", ""),
                        source=product.get("source", source),
                        price=product.get("price", ""),
                        image_url=product.get("image_url", "")
                    )
                    if notion_url:
                        update_product_notion(product_id, notion_url)
                        product["notion_url"] = notion_url
                    bio_url = self.notion.get_public_url() or notion_url

                if not bio_url and self.linktree.is_ready():
                    linktree_url = self.linktree.publish_link(
                        product_name=product.get("name_en") or product.get("name", ""),
                        product_code=product.get("product_code", ""),
                        url=affiliate_link or product.get("link", ""),
                        source=product.get("source", source)
                    )
                    if linktree_url:
                        update_product_linktree(product_id, linktree_url)
                        product["linktree_url"] = linktree_url
                        bio_url = linktree_url

                for video in edited_videos:
                    media_id = self.social.upload_reel(
                        video_path=video["edited_path"],
                        product_name=product.get("name_en") or product.get("name", ""),
                        product_id=product_id,
                        video_id=video.get("id"),
                        product_code=product.get("product_code", ""),
                        cta_keyword=product.get("cta_keyword", "")
                    )
                    if media_id:
                        stats["posts"] += 1
                        self.notifier.notify_upload_success(
                            product.get("name_en") or product.get("name", ""),
                            media_id
                        )
                        if monitor_comments:
                            logger.info(
                                f"[STEP 5/5] 댓글 모니터링: "
                                f"{product.get('name_en') or product.get('name')} "
                                f"({monitor_duration}분)"
                            )
                            engagement = self.social.monitor_comments(
                                media_id=media_id,
                                product_name=product.get("name_en") or product.get("name", ""),
                                product_code=product.get("product_code", ""),
                                affiliate_link=affiliate_link,
                                bio_url=bio_url,
                                cta_keyword=product.get("cta_keyword", ""),
                                duration_minutes=monitor_duration
                            )
                            stats["dms"] += engagement.get("dms", 0)

            except Exception as e:
                error_msg = f"Video-First 처리 실패 [{kw}]: {e}"
                logger.error(error_msg)
                stats["errors"].append(error_msg)
                continue

        finish_run_log(
            run_id,
            products=stats["products"],
            videos=stats["videos"],
            posts=stats["posts"],
            dms=stats["dms"],
            status="completed"
        )
        return stats

    async def _run_product_first_video_required(self, run_id: int, stats: dict,
                                                max_products: int,
                                                monitor_comments: bool,
                                                monitor_duration: int,
                                                keyword: str | None,
                                                source: str = "aliexpress",
                                                max_videos: int | None = None,
                                                finalize_log: bool = True) -> dict:
        """
        Product-First: AliExpress 상품 소싱 → 브랜드/제품명으로 영상 검색
        영상 없으면 다른 상품으로 반복
        """
        keyword_stream = self._video_first_keyword_stream(keyword)
        seen_links = set()

        # 하루 제한
        today_count = get_today_product_count()
        remaining = max(0, MAX_DAILY_PRODUCTS - today_count)
        if remaining <= 0:
            msg = f"하루 최대 상품 수({MAX_DAILY_PRODUCTS})를 초과하여 종료합니다."
            logger.warning(msg)
            stats["errors"].append(msg)
            finish_run_log(run_id, status="failed", error=msg)
            return stats

        max_products = min(max_products, remaining)

        if max_videos is None:
            max_videos = VIDEO_FIRST_MAX_VIDEOS

        while stats["products"] < max_products:
            kw = next(keyword_stream)
            logger.info(f"\n[STEP 1/5] 상품 소싱 키워드: {kw}")

            candidates = self.sourcer.search_aliexpress_products(
                keyword=kw,
                max_items=PRODUCT_FIRST_MAX_CANDIDATES
            )
            if not candidates:
                logger.warning(f"상품 소싱 실패(0개): {kw}")
                continue

            for product in candidates:
                link = product.get("link") or product.get("affiliate_link") or ""
                if link and link in seen_links:
                    continue
                if link:
                    seen_links.add(link)

                # 브랜드/제품명 기반 검색 키워드 생성
                base_name = product.get("name", "")
                video_keywords = self.sourcer.build_video_search_keywords_for_product(base_name)
                if not video_keywords:
                    video_keywords = [base_name] if base_name else []

                product["video_keywords"] = video_keywords

                logger.info(f"[STEP 2/5] 영상 검색 키워드: {video_keywords}")
                videos = self.miner.run_mining_pipeline(
                    product,
                    max_videos=max_videos
                )

                if len(videos) < PRODUCT_FIRST_MIN_VIDEOS:
                    logger.warning(
                        f"영상 없음({len(videos)}/{PRODUCT_FIRST_MIN_VIDEOS}) - 다른 상품으로"
                    )
                    continue

                stats["videos"] += len(videos)

                # 상품 분석/저장
                analysis = self.sourcer.analyze_product_by_name(product.get("name", ""))
                product["name_en"] = analysis.get("product_name", "")
                product["keywords"] = analysis.get("keywords", [])
                product["cta_keyword"] = self.sourcer.infer_cta_keyword(
                    product.get("name_en") or product.get("name", ""),
                    product.get("keywords", [])
                )

                product_id = insert_product(
                    name=product["name"],
                    name_en=product["name_en"],
                    keywords=product["keywords"],
                    image_url=product.get("image_url", ""),
                    price=product.get("price", ""),
                    affiliate_link=product.get("affiliate_link", product.get("link", "")),
                    source=product.get("source", source),
                    cta_keyword=product.get("cta_keyword", "")
                )
                product_code = self.sourcer._generate_product_code(
                    product_id,
                    product.get("source", source)
                )
                update_product_code(product_id, product_code)
                product["id"] = product_id
                product["product_code"] = product_code

                stats["products"] += 1

                # 영상에 product_id 연결
                for v in videos:
                    vid = v.get("id")
                    if vid:
                        update_video_product(vid, product_id)

                # ── STEP 3: 영상 편집 ──
                logger.info(f"[STEP 3/5] 영상 편집: {product.get('name_en')}")
                edited_videos = self.editor.batch_edit(
                    videos, product_name=product.get("name_en") or product.get("name", "")
                )
                if not edited_videos:
                    logger.warning("편집된 영상 없음")
                    break

                # ── STEP 4: 업로드 ──
                affiliate_link = (
                    product.get("affiliate_link")
                    or InstagramManager.generate_affiliate_link(product.get("link", ""))
                )
                if affiliate_link:
                    update_product_affiliate_link(product_id, affiliate_link)

                bio_url = ""
                if self.notion.is_ready():
                    notion_url = self.notion.upsert_product(
                        product_code=product.get("product_code", ""),
                        product_name=product.get("name_en") or product.get("name", ""),
                        link_url=affiliate_link or product.get("link", ""),
                        source=product.get("source", source),
                        price=product.get("price", ""),
                        image_url=product.get("image_url", "")
                    )
                    if notion_url:
                        update_product_notion(product_id, notion_url)
                        product["notion_url"] = notion_url
                    bio_url = self.notion.get_public_url() or notion_url

                if not bio_url and self.linktree.is_ready():
                    linktree_url = self.linktree.publish_link(
                        product_name=product.get("name_en") or product.get("name", ""),
                        product_code=product.get("product_code", ""),
                        url=affiliate_link or product.get("link", ""),
                        source=product.get("source", source)
                    )
                    if linktree_url:
                        update_product_linktree(product_id, linktree_url)
                        product["linktree_url"] = linktree_url
                        bio_url = linktree_url

                for video in edited_videos:
                    media_id = self.social.upload_reel(
                        video_path=video["edited_path"],
                        product_name=product.get("name_en") or product.get("name", ""),
                        product_id=product_id,
                        video_id=video.get("id"),
                        product_code=product.get("product_code", ""),
                        cta_keyword=product.get("cta_keyword", "")
                    )
                    if media_id:
                        stats["posts"] += 1
                        self.notifier.notify_upload_success(
                            product.get("name_en") or product.get("name", ""),
                            media_id
                        )
                        if monitor_comments:
                            logger.info(
                                f"[STEP 5/5] 댓글 모니터링: "
                                f"{product.get('name_en') or product.get('name')} "
                                f"({monitor_duration}분)"
                            )
                            engagement = self.social.monitor_comments(
                                media_id=media_id,
                                product_name=product.get("name_en") or product.get("name", ""),
                                product_code=product.get("product_code", ""),
                                affiliate_link=affiliate_link,
                                bio_url=bio_url,
                                cta_keyword=product.get("cta_keyword", ""),
                                duration_minutes=monitor_duration
                            )
                            stats["dms"] += engagement.get("dms", 0)

                # 다음 상품으로 진행
                break

        if finalize_log:
            finish_run_log(
                run_id,
                products=stats["products"],
                videos=stats["videos"],
                posts=stats["posts"],
                dms=stats["dms"],
                status="completed"
            )
        return stats

    async def _run_daily_two_categories(self, run_id: int, stats: dict,
                                        monitor_comments: bool,
                                        monitor_duration: int,
                                        source: str = "aliexpress") -> dict:
        """
        하루 2개 고정 모드:
        1) 생활꿀템 1개
        2) 계절용품 1개
        """
        lifestyle_pool = LIFESTYLE_KEYWORD_POOL or ALIEXPRESS_KEYWORD_POOL
        seasonal_pool = get_seasonal_keyword_pool() or []

        lifestyle_kw = (
            pick_daily_from_pool_key(lifestyle_pool, "lifestyle")
            or get_daily_trend_keyword()
            or ALIEXPRESS_DEFAULT_KEYWORD
        )
        seasonal_kw = (
            pick_daily_from_pool_key(seasonal_pool, "seasonal")
            or get_daily_seasonal_keyword()
            or get_daily_trend_keyword()
            or ALIEXPRESS_DEFAULT_KEYWORD
        )

        categories = [
            ("생활꿀템", lifestyle_kw),
            ("계절용품", seasonal_kw),
        ]

        for label, kw in categories:
            if not kw:
                continue
            logger.info(f"\n[DAILY] {label} 키워드: {kw}")
            await self._run_product_first_video_required(
                run_id=run_id,
                stats=stats,
                max_products=1,
                monitor_comments=monitor_comments,
                monitor_duration=monitor_duration,
                keyword=kw,
                source=source,
                max_videos=DAILY_TWO_MAX_VIDEOS_PER_PRODUCT,
                finalize_log=False,
            )

        finish_run_log(
            run_id,
            products=stats["products"],
            videos=stats["videos"],
            posts=stats["posts"],
            dms=stats["dms"],
            status="completed"
        )
        return stats

    def _video_first_keyword_stream(self, seed_keyword: str | None):
        """새로운 키워드를 계속 생성하는 무한 스트림"""
        while True:
            batch = self._build_video_first_keywords(seed_keyword)
            if not batch:
                if ALIEXPRESS_DEFAULT_KEYWORD:
                    batch = [ALIEXPRESS_DEFAULT_KEYWORD]
                else:
                    batch = []

            random.shuffle(batch)
            for kw in batch:
                if kw:
                    yield kw
            # 한 바퀴 돌면 다시 새로운 배치를 만들어 계속 반복

    def _build_video_first_keywords(self, seed_keyword: str | None) -> list[str]:
        keys: list[str] = []
        if seed_keyword:
            if BRAND_MODEL_ENRICH:
                expanded = self.sourcer.expand_brand_model_keywords(seed_keyword)
                if expanded:
                    keys.extend(expanded)
                else:
                    keys.append(seed_keyword)
            else:
                keys.append(seed_keyword)
        # 생활용품 풀 + 트렌드 + fallback
        keys.extend(ALIEXPRESS_KEYWORD_POOL or [])
        keys.extend(get_daily_trend_keywords() or [])
        keys.extend(TREND_FALLBACK_KEYWORDS or [])
        if ALIEXPRESS_DEFAULT_KEYWORD:
            keys.append(ALIEXPRESS_DEFAULT_KEYWORD)

        # 중복 제거 (순서 유지)
        seen = set()
        deduped = []
        for k in keys:
            k = (k or "").strip()
            if not k:
                continue
            if k.lower() in seen:
                continue
            seen.add(k.lower())
            deduped.append(k)
        return deduped

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
