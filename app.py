"""
app.py - Streamlit 대시보드
SNS 마케팅 자동화 봇의 제어 패널 및 모니터링 UI
"""

import streamlit as st
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 path에 추가
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, INSTAGRAM_USERNAME,
    COUPANG_GOLDBOX_URL, COUPANG_RANKING_URL,
    MAX_PRODUCTS_PER_RUN, DATA_DIR, MAX_DAILY_PRODUCTS,
    ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID,
    LINKTREE_MODE, LINKTREE_WEBHOOK_URL,
    TREND_SOURCE, TREND_GEO,
    NOTION_TOKEN, NOTION_DATABASE_ID, NOTION_PUBLIC_URL,
    IG_API_MODE, IG_USER_ID, IG_ACCESS_TOKEN,
    VIDEO_HOSTING, CLOUDINARY_CLOUD_NAME, VIDEO_PUBLIC_BASE_URL,
    INSTAGRAM_PASSWORD,
    ALIEXPRESS_KEYWORD_POOL, ALIEXPRESS_EXCLUDE_KEYWORDS
)
from core.database import get_stats, get_recent_logs, get_connection
from core.pipeline import AutomationPipeline
from core.bot import TelegramNotifier

# ──────────────────────────────────────────────
# 페이지 설정
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="SNS 마케팅 자동화 봇",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ──────────────────────────────────────────────
# 사이드바
# ──────────────────────────────────────────────
with st.sidebar:
    st.title("🤖 SNS 마케팅 봇")
    st.markdown("---")

    page = st.radio(
        "메뉴",
        ["📊 대시보드", "🚀 수동 실행", "📦 상품 관리",
         "🎬 영상 관리", "📱 게시물 관리", "⚙️ 설정"],
        index=0
    )

    st.markdown("---")

    # 연결 상태 표시
    st.subheader("연결 상태")
    col1, col2 = st.columns(2)
    with col1:
        if OPENAI_API_KEY:
            st.success("OpenAI ✓")
        else:
            st.error("OpenAI ✗")
    with col2:
        if TELEGRAM_BOT_TOKEN:
            st.success("Telegram ✓")
        else:
            st.error("Telegram ✗")

    if IG_API_MODE == "graph":
        if IG_USER_ID and IG_ACCESS_TOKEN:
            st.success("Instagram Graph API ✓")
        else:
            st.error("Instagram Graph API ✗")
    elif IG_API_MODE == "instagrapi":
        if INSTAGRAM_USERNAME:
            st.success(f"Instagram: @{INSTAGRAM_USERNAME}")
        else:
            st.warning("Instagram: 미설정")
    else:
        st.warning("Instagram API: 비활성화")


# ──────────────────────────────────────────────
# 대시보드 페이지
# ──────────────────────────────────────────────
if page == "📊 대시보드":
    st.title("📊 대시보드")
    st.markdown("실시간 봇 운영 현황을 확인하세요.")

    # 통계 카드
    stats = get_stats()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("총 상품", f"{stats['total_products']}개")
    col2.metric("총 영상", f"{stats['total_videos']}개")
    col3.metric("총 게시물", f"{stats['total_posts']}개")
    col4.metric("총 상호작용", f"{stats['total_interactions']}개")
    col5.metric("총 DM", f"{stats['total_dms']}개")

    st.markdown("---")

    # 최근 실행 기록
    st.subheader("📋 최근 실행 기록")
    logs = get_recent_logs(limit=10)

    if logs:
        for log in logs:
            status_emoji = {
                "completed": "✅",
                "running": "🔄",
                "failed": "❌"
            }.get(log.get("status", ""), "❓")

            with st.expander(
                f"{status_emoji} [{log.get('run_type', 'N/A')}] "
                f"{log.get('started_at', 'N/A')[:16]}"
            ):
                col1, col2, col3, col4 = st.columns(4)
                col1.write(f"**상품:** {log.get('products_processed', 0)}개")
                col2.write(f"**영상:** {log.get('videos_created', 0)}개")
                col3.write(f"**업로드:** {log.get('posts_uploaded', 0)}개")
                col4.write(f"**DM:** {log.get('dms_sent', 0)}개")

                if log.get("error_message"):
                    st.error(f"오류: {log['error_message']}")
    else:
        st.info("아직 실행 기록이 없습니다.")


# ──────────────────────────────────────────────
# 수동 실행 페이지
# ──────────────────────────────────────────────
elif page == "🚀 수동 실행":
    st.title("🚀 수동 실행")
    st.markdown("파이프라인을 수동으로 실행합니다.")

    # 실행 옵션
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("실행 설정")
        source_options = ["쿠팡 골드박스", "쿠팡 랭킹", "알리익스프레스 API", "커스텀 URL"]
        default_index = source_options.index("알리익스프레스 API")
        source_option = st.selectbox(
            "소싱 소스",
            source_options,
            index=default_index
        )

        source_type = "coupang"
        ali_keyword = None

        if source_option == "알리익스프레스 API":
            source_type = "aliexpress"
            source_url = None
            ali_keyword = st.text_input(
                "알리익스프레스 검색 키워드 (비워두면 한국 트렌드 자동)",
                ""
            )
        elif source_option == "커스텀 URL":
            source_url = st.text_input("URL 입력", "")
        elif source_option == "쿠팡 랭킹":
            source_url = COUPANG_RANKING_URL
        else:
            source_url = COUPANG_GOLDBOX_URL

        max_products = st.slider(
            "처리할 상품 수",
            1, 10,
            min(MAX_PRODUCTS_PER_RUN, MAX_DAILY_PRODUCTS)
        )
        monitor_comments = st.checkbox("댓글 모니터링 활성화", value=True)
        monitor_duration = st.slider("모니터링 시간 (분)", 5, 120, 30)

    with col2:
        st.subheader("실행 모드")
        run_mode = st.radio(
            "모드 선택",
            ["전체 파이프라인", "소싱만", "편집만", "업로드만"]
        )

    st.markdown("---")

    # 전체 파이프라인 실행
    if run_mode == "전체 파이프라인":
        if st.button("🚀 전체 파이프라인 실행", type="primary", use_container_width=True):
            with st.spinner("파이프라인 실행 중... (시간이 소요될 수 있습니다)"):
                pipeline = AutomationPipeline()
                result = asyncio.run(
                    pipeline.run_full_pipeline(
                        source_url=source_url,
                        max_products=max_products,
                        source=source_type,
                        keyword=ali_keyword,
                        monitor_comments=monitor_comments,
                        monitor_duration=monitor_duration
                    )
                )

                st.success("파이프라인 실행 완료!")
                st.json(result)

    # 소싱만 실행
    elif run_mode == "소싱만":
        if st.button("📦 소싱 실행", type="primary", use_container_width=True):
            with st.spinner("상품 소싱 중..."):
                pipeline = AutomationPipeline()
                products = asyncio.run(
                    pipeline.run_sourcing_only(
                        url=source_url,
                        source=source_type,
                        keyword=ali_keyword,
                        max_products=max_products
                    )
                )

                st.success(f"{len(products)}개 상품 소싱 완료!")
                for p in products:
                    with st.expander(f"📦 {p.get('name', 'Unknown')[:40]}"):
                        st.write(f"**영문명:** {p.get('name_en', 'N/A')}")
                        st.write(f"**키워드:** {', '.join(p.get('keywords', []))}")
                        st.write(f"**가격:** {p.get('price', 'N/A')}")
                        if p.get("image_url"):
                            st.image(p["image_url"], width=200)

    # 편집만 실행
    elif run_mode == "편집만":
        video_file = st.file_uploader("영상 파일 업로드", type=["mp4", "mov", "avi"])
        product_name = st.text_input("상품명 (후킹 문구용)", "")

        if video_file and st.button("🎬 편집 실행", type="primary"):
            # 임시 파일 저장
            temp_path = DATA_DIR / f"temp_{video_file.name}"
            with open(temp_path, "wb") as f:
                f.write(video_file.read())

            with st.spinner("영상 편집 중..."):
                pipeline = AutomationPipeline()
                edited_path = pipeline.run_editing_only(
                    str(temp_path), product_name
                )

                if edited_path:
                    st.success("편집 완료!")
                    st.video(edited_path)
                else:
                    st.error("편집 실패")

    # 업로드만 실행
    elif run_mode == "업로드만":
        video_file = st.file_uploader("편집된 영상 파일", type=["mp4"])
        product_name = st.text_input("상품명", "")
        custom_caption = st.text_area("캡션 (비워두면 GPT 자동 생성)", "")

        if video_file and st.button("📱 업로드 실행", type="primary"):
            temp_path = DATA_DIR / f"upload_{video_file.name}"
            with open(temp_path, "wb") as f:
                f.write(video_file.read())

            with st.spinner("인스타그램 업로드 중..."):
                pipeline = AutomationPipeline()
                media_id = pipeline.run_upload_only(
                    str(temp_path), product_name
                )

                if media_id:
                    st.success(f"업로드 성공! Media ID: {media_id}")
                else:
                    st.error("업로드 실패")


# ──────────────────────────────────────────────
# 상품 관리 페이지
# ──────────────────────────────────────────────
elif page == "📦 상품 관리":
    st.title("📦 상품 관리")

    conn = get_connection()
    products = conn.execute(
        "SELECT * FROM products ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()

    if products:
        for p in products:
            p = dict(p)
            with st.expander(
                f"[{p['id']}] {p['name'][:40]}... | "
                f"{p.get('price', 'N/A')} | {p.get('status', 'N/A')}"
            ):
                col1, col2 = st.columns([1, 2])
                with col1:
                    if p.get("image_url"):
                        st.image(p["image_url"], width=150)
                with col2:
                    if p.get("product_code"):
                        st.write(f"**제품번호:** {p.get('product_code')}")
                    st.write(f"**한국어명:** {p['name']}")
                    st.write(f"**영문명:** {p.get('name_en', 'N/A')}")
                    keywords = json.loads(p.get("keywords", "[]"))
                    st.write(f"**키워드:** {', '.join(keywords)}")
                    st.write(f"**가격:** {p.get('price', 'N/A')}")
                    if p.get("affiliate_link"):
                        st.write(f"**제휴링크:** {p.get('affiliate_link')}")
                    if p.get("notion_url"):
                        st.write(f"**Notion:** {p.get('notion_url')}")
                    if p.get("linktree_url"):
                        st.write(f"**Linktree:** {p.get('linktree_url')}")
                    st.write(f"**소싱일:** {p.get('created_at', 'N/A')}")
    else:
        st.info("소싱된 상품이 없습니다.")


# ──────────────────────────────────────────────
# 영상 관리 페이지
# ──────────────────────────────────────────────
elif page == "🎬 영상 관리":
    st.title("🎬 영상 관리")

    conn = get_connection()
    videos = conn.execute(
        """SELECT v.*, p.name as product_name
           FROM videos v
           LEFT JOIN products p ON v.product_id = p.id
           ORDER BY v.id DESC LIMIT 50"""
    ).fetchall()
    conn.close()

    if videos:
        for v in videos:
            v = dict(v)
            status_emoji = "✅" if v.get("status") == "edited" else "📥"
            with st.expander(
                f"{status_emoji} [{v['id']}] {v.get('product_name', 'N/A')[:30]} | "
                f"{v.get('platform', 'N/A')} | "
                f"조회수: {v.get('view_count', 0):,}"
            ):
                st.write(f"**플랫폼:** {v.get('platform', 'N/A')}")
                st.write(f"**원본 URL:** {v.get('original_url', 'N/A')}")
                st.write(f"**조회수:** {v.get('view_count', 0):,}")
                st.write(f"**좋아요:** {v.get('like_count', 0):,}")
                st.write(f"**길이:** {v.get('duration', 0)}초")
                st.write(f"**상태:** {v.get('status', 'N/A')}")

                if v.get("edited_path") and Path(v["edited_path"]).exists():
                    st.video(v["edited_path"])
    else:
        st.info("다운로드된 영상이 없습니다.")


# ──────────────────────────────────────────────
# 게시물 관리 페이지
# ──────────────────────────────────────────────
elif page == "📱 게시물 관리":
    st.title("📱 게시물 관리")

    conn = get_connection()
    posts = conn.execute(
        """SELECT po.*, p.name as product_name
           FROM posts po
           LEFT JOIN products p ON po.product_id = p.id
           ORDER BY po.id DESC LIMIT 50"""
    ).fetchall()
    conn.close()

    if posts:
        for post in posts:
            post = dict(post)
            with st.expander(
                f"[{post['id']}] {post.get('product_name', 'N/A')[:30]} | "
                f"{post.get('upload_time', 'N/A')[:16]}"
            ):
                st.write(f"**Media ID:** {post.get('post_id', 'N/A')}")
                st.write(f"**캡션:**\n{post.get('caption', 'N/A')}")
                st.write(f"**해시태그:** {post.get('hashtags', 'N/A')}")
                st.write(f"**상태:** {post.get('status', 'N/A')}")

                # 해당 게시물의 상호작용
                conn = get_connection()
                interactions = conn.execute(
                    "SELECT * FROM interactions WHERE post_id = ?",
                    (post['id'],)
                ).fetchall()
                conn.close()

                if interactions:
                    st.write(f"**댓글 처리:** {len(interactions)}건")
                    for i in [dict(x) for x in interactions]:
                        dm_icon = "✉️" if i.get("dm_sent") else "💬"
                        st.write(
                            f"  {dm_icon} @{i.get('commenter_username', 'N/A')}: "
                            f"{i.get('comment_text', '')[:50]}"
                        )
    else:
        st.info("업로드된 게시물이 없습니다.")


# ──────────────────────────────────────────────
# 설정 페이지
# ──────────────────────────────────────────────
elif page == "⚙️ 설정":
    st.title("⚙️ 설정")
    st.markdown(
        "API 키와 자격증명은 `.streamlit/secrets.toml` 또는 "
        "환경변수로 설정하세요."
    )

    st.subheader("현재 설정 상태")

    settings = {
        "OpenAI API Key": "✅ 설정됨" if OPENAI_API_KEY else "❌ 미설정",
        "Telegram Bot Token": "✅ 설정됨" if TELEGRAM_BOT_TOKEN else "❌ 미설정",
        "Instagram API Mode": IG_API_MODE or "미설정",
        "Instagram Username": INSTAGRAM_USERNAME or "❌ 미설정",
        "Instagram Password": "✅ 설정됨" if INSTAGRAM_PASSWORD else "❌ 미설정",
        "IG User ID": "✅ 설정됨" if IG_USER_ID else "❌ 미설정",
        "IG Access Token": "✅ 설정됨" if IG_ACCESS_TOKEN else "❌ 미설정",
        "Video Hosting": VIDEO_HOSTING or "미설정",
        "Cloudinary": "✅ 설정됨" if CLOUDINARY_CLOUD_NAME else "❌ 미설정",
        "Public Video URL Base": VIDEO_PUBLIC_BASE_URL or "❌ 미설정",
        "AliExpress App Key": "✅ 설정됨" if ALIEXPRESS_APP_KEY else "❌ 미설정",
        "AliExpress App Secret": "✅ 설정됨" if ALIEXPRESS_APP_SECRET else "❌ 미설정",
        "AliExpress Tracking ID": "✅ 설정됨" if ALIEXPRESS_TRACKING_ID else "❌ 미설정",
        "AliExpress Keyword Pool": f"{len(ALIEXPRESS_KEYWORD_POOL)}개",
        "AliExpress Exclude Keywords": f"{len(ALIEXPRESS_EXCLUDE_KEYWORDS)}개",
        "Daily Product Limit": f"{MAX_DAILY_PRODUCTS}개/일",
        "Linktree Mode": LINKTREE_MODE or "미설정",
        "Linktree Webhook": "✅ 설정됨" if LINKTREE_WEBHOOK_URL else "❌ 미설정",
        "Trend Source": TREND_SOURCE or "미설정",
        "Trend Geo": TREND_GEO or "미설정",
        "Notion Token": "✅ 설정됨" if NOTION_TOKEN else "❌ 미설정",
        "Notion Database": "✅ 설정됨" if NOTION_DATABASE_ID else "❌ 미설정",
        "Notion Public URL": NOTION_PUBLIC_URL or "❌ 미설정",
    }

    for key, value in settings.items():
        st.write(f"**{key}:** {value}")

    st.markdown("---")

    st.subheader("Telegram 테스트")
    if st.button("📨 테스트 메시지 발송"):
        notifier = TelegramNotifier()
        success = notifier.send_message("🧪 Streamlit 대시보드에서 보낸 테스트 메시지입니다!")
        if success:
            st.success("테스트 메시지 발송 성공!")
        else:
            st.error("발송 실패 - Telegram 설정을 확인하세요.")

    st.markdown("---")

    st.subheader("데이터베이스 관리")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🗑️ 실행 로그 초기화", type="secondary"):
            conn = get_connection()
            conn.execute("DELETE FROM run_logs")
            conn.commit()
            conn.close()
            st.success("실행 로그가 초기화되었습니다.")
            st.rerun()

    with col2:
        if st.button("🗑️ 전체 데이터 초기화", type="secondary"):
            conn = get_connection()
            for table in ["interactions", "posts", "videos", "products", "run_logs"]:
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
            conn.close()
            st.success("전체 데이터가 초기화되었습니다.")
            st.rerun()

    st.markdown("---")

    st.subheader("secrets.toml 설정 가이드")
    st.code("""
# .streamlit/secrets.toml

OPENAI_API_KEY = "sk-..."
TELEGRAM_BOT_TOKEN = "123456:ABC-DEF..."
TELEGRAM_CHAT_ID = "123456789"
INSTAGRAM_USERNAME = "your_username"
INSTAGRAM_PASSWORD = "your_password"

# Instagram Graph API (권장)
IG_API_MODE = "graph"  # graph | instagrapi | disabled
IG_GRAPH_API_VERSION = "v20.0"
IG_GRAPH_HOST = "graph.facebook.com"
IG_MESSAGE_HOST = "graph.facebook.com"
IG_USER_ID = "your_ig_user_id"
IG_ACCESS_TOKEN = "your_long_lived_access_token"
IG_SHARE_TO_FEED = "false"

# Video Hosting (Graph API 업로드용)
VIDEO_HOSTING = "cloudinary"  # cloudinary | public_url | none
VIDEO_PUBLIC_BASE_URL = "https://your-public-video-host.com/videos"
CLOUDINARY_CLOUD_NAME = "your_cloud_name"
CLOUDINARY_API_KEY = "your_cloudinary_api_key"
CLOUDINARY_API_SECRET = "your_cloudinary_api_secret"
CLOUDINARY_FOLDER = "sns-marketing-bot"
COUPANG_ACCESS_KEY = "your_access_key"
COUPANG_SECRET_KEY = "your_secret_key"
COUPANG_PARTNER_ID = "your_partner_id"

# AliExpress Open Platform
ALIEXPRESS_APP_KEY = "your_app_key"
ALIEXPRESS_APP_SECRET = "your_app_secret"
ALIEXPRESS_TRACKING_ID = "your_tracking_id"
ALIEXPRESS_LANGUAGE = "EN"   # optional, default EN
ALIEXPRESS_CURRENCY = "USD"  # optional, default USD
ALIEXPRESS_DEFAULT_KEYWORD = "kitchen gadget"

# Linktree Webhook (자동 업로드)
LINKTREE_MODE = "webhook"  # webhook | queue | disabled
LINKTREE_WEBHOOK_URL = "https://your-webhook-url"
LINKTREE_WEBHOOK_SECRET = "your-secret"

# Trend keyword settings (KR)
TREND_SOURCE = "google_trends"  # google_trends | fallback
TREND_GEO = "KR"
TREND_MAX_ITEMS = "20"
TREND_FALLBACK_KEYWORDS = "가성비 전자제품,주방 꿀템,홈카페 용품,운동용품,스킨케어"

# Notion (Link-in-bio)
NOTION_TOKEN = "secret_xxx"
NOTION_DATABASE_ID = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
NOTION_PUBLIC_URL = "https://www.notion.so/your-page"
NOTION_PROP_NAME = "Name"
NOTION_PROP_CODE = "Product Code"
NOTION_PROP_LINK = "Link"
NOTION_PROP_SOURCE = "Source"
NOTION_PROP_PRICE = "Price"
NOTION_PROP_IMAGE = "Image"
    """, language="toml")
