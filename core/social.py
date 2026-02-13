"""
social.py - 소셜 미디어 자동화 모듈
인스타그램 릴스 업로드, 댓글 모니터링, 대댓글 + DM 발송을 처리합니다.
"""

import json
import logging
import random
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    OPENAI_API_KEY, INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD,
    INSTAGRAM_SESSION_FILE, CAPTION_PROMPT, REPLY_TEMPLATES,
    DM_TEMPLATE, COMMENT_POLL_INTERVAL, MAX_DM_PER_HOUR,
    SESSIONS_DIR
)
from core.database import insert_post, insert_interaction, mark_interaction_replied

logger = logging.getLogger(__name__)


class InstagramManager:
    """인스타그램 자동화 관리 클래스"""

    def __init__(self):
        self.client = None
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.dm_count_this_hour = 0
        self._login()

    # ──────────────────────────────────────────
    # 로그인 및 세션 관리
    # ──────────────────────────────────────────

    def _login(self):
        """인스타그램 로그인 (세션 파일 재사용)"""
        try:
            from instagrapi import Client
            from instagrapi.exceptions import LoginRequired, ChallengeRequired

            self.client = Client()

            # 세션 파일 설정
            session_file = Path(INSTAGRAM_SESSION_FILE)

            # 기존 세션 복원 시도
            if session_file.exists():
                try:
                    self.client.load_settings(str(session_file))
                    self.client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)

                    # 세션 유효성 검증
                    self.client.get_timeline_feed()
                    logger.info("기존 세션으로 로그인 성공")
                    return

                except (LoginRequired, ChallengeRequired) as e:
                    logger.warning(f"세션 만료, 재로그인 필요: {e}")
                    # 세션 파일 삭제 후 재시도
                    session_file.unlink(missing_ok=True)
                    self.client = Client()

                except Exception as e:
                    logger.warning(f"세션 복원 실패: {e}")
                    self.client = Client()

            # 새로 로그인
            if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
                # Challenge 대응 설정
                self.client.delay_range = [2, 5]  # 요청 간 딜레이

                self.client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)

                # 세션 저장
                self.client.dump_settings(str(session_file))
                logger.info("새 세션으로 로그인 성공, 세션 파일 저장 완료")
            else:
                logger.warning("인스타그램 자격증명이 설정되지 않았습니다.")
                self.client = None

        except ImportError:
            logger.error("instagrapi가 설치되지 않았습니다. pip install instagrapi")
            self.client = None
        except Exception as e:
            logger.error(f"인스타그램 로그인 실패: {e}")
            self.client = None

    def is_logged_in(self) -> bool:
        """로그인 상태 확인"""
        return self.client is not None

    # ──────────────────────────────────────────
    # GPT 캡션 생성
    # ──────────────────────────────────────────

    def generate_caption(self, product_name: str) -> tuple[str, str]:
        """
        GPT로 인스타그램 릴스 캡션 + 해시태그 생성
        Returns: (caption, hashtags)
        """
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": CAPTION_PROMPT.format(product_name=product_name)
                }],
                max_tokens=500,
                temperature=0.8
            )

            full_text = response.choices[0].message.content.strip()

            # 해시태그 분리
            lines = full_text.split("\n")
            hashtag_line = ""
            caption_lines = []

            for line in lines:
                if line.strip().startswith("#"):
                    hashtag_line = line.strip()
                else:
                    caption_lines.append(line)

            caption = "\n".join(caption_lines).strip()
            hashtags = hashtag_line if hashtag_line else "#추천 #꿀템 #쇼핑 #리뷰"

            logger.info(f"캡션 생성 완료 ({len(caption)}자)")
            return caption, hashtags

        except Exception as e:
            logger.error(f"캡션 생성 실패: {e}")
            default_caption = (
                f"요즘 핫한 {product_name} 리뷰! 🔥\n"
                f"궁금하면 댓글 달아주세요! 💬"
            )
            default_hashtags = "#추천 #꿀템 #쇼핑 #리뷰 #핫딜 #가성비 #인기템 #쇼핑추천 #신상 #트렌드"
            return default_caption, default_hashtags

    # ──────────────────────────────────────────
    # 릴스 업로드
    # ──────────────────────────────────────────

    def upload_reel(self, video_path: str, product_name: str = "",
                    product_id: int = None, video_id: int = None,
                    caption: str = None, hashtags: str = None) -> Optional[str]:
        """
        인스타그램 릴스 업로드
        Args:
            video_path: 편집된 영상 파일 경로
            product_name: 상품명
            product_id: DB 상품 ID
            video_id: DB 영상 ID
            caption: 커스텀 캡션 (없으면 GPT 생성)
            hashtags: 커스텀 해시태그

        Returns: 게시물 media_id 또는 None
        """
        if not self.is_logged_in():
            logger.error("인스타그램 미로그인 상태")
            return None

        video_path = Path(video_path)
        if not video_path.exists():
            logger.error(f"영상 파일 없음: {video_path}")
            return None

        try:
            # 캡션 생성
            if not caption or not hashtags:
                gen_caption, gen_hashtags = self.generate_caption(product_name)
                caption = caption or gen_caption
                hashtags = hashtags or gen_hashtags

            full_caption = f"{caption}\n\n{hashtags}"

            logger.info(f"릴스 업로드 시작: {video_path.name}")

            # 업로드
            media = self.client.clip_upload(
                path=str(video_path),
                caption=full_caption,
            )

            media_id = str(media.pk)
            logger.info(f"릴스 업로드 성공! Media ID: {media_id}")

            # DB 저장
            if product_id and video_id:
                insert_post(
                    product_id=product_id,
                    video_id=video_id,
                    post_id=media_id,
                    caption=caption,
                    hashtags=hashtags
                )

            return media_id

        except Exception as e:
            logger.error(f"릴스 업로드 실패: {e}")
            return None

    # ──────────────────────────────────────────
    # 댓글 모니터링 및 자동 응답
    # ──────────────────────────────────────────

    def monitor_comments(self, media_id: str, product_name: str = "",
                         product_code: str = "",
                         affiliate_link: str = "",
                         bio_url: str = "",
                         duration_minutes: int = 60) -> dict:
        """
        게시물 댓글 모니터링 및 자동 대댓글 + DM 발송
        Args:
            media_id: 인스타그램 게시물 ID
            product_name: 상품명
            affiliate_link: 제휴 링크
            duration_minutes: 모니터링 지속 시간 (분)

        Returns: {"replies": int, "dms": int}
        """
        if not self.is_logged_in():
            logger.error("인스타그램 미로그인 상태")
            return {"replies": 0, "dms": 0}

        stats = {"replies": 0, "dms": 0}
        processed_comment_ids = set()
        end_time = time.time() + (duration_minutes * 60)

        logger.info(f"댓글 모니터링 시작 (Media: {media_id}, {duration_minutes}분간)")

        while time.time() < end_time:
            try:
                # 댓글 가져오기
                comments = self.client.media_comments(media_id, amount=50)

                for comment in comments:
                    comment_id = str(comment.pk)

                    if comment_id in processed_comment_ids:
                        continue

                    # 자기 댓글은 스킵
                    if comment.user.username == INSTAGRAM_USERNAME:
                        processed_comment_ids.add(comment_id)
                        continue

                    logger.info(
                        f"새 댓글 발견: @{comment.user.username}: "
                        f"{comment.text[:50]}..."
                    )

                    # 1. 대댓글 달기
                    reply_text = random.choice(REPLY_TEMPLATES)
                    try:
                        self.client.media_comment(
                            media_id,
                            reply_text,
                            replied_to_comment_id=comment.pk
                        )
                        stats["replies"] += 1
                        logger.info(f"대댓글 완료: '{reply_text}'")
                    except Exception as e:
                        logger.warning(f"대댓글 실패: {e}")

                    # 2. DM 발송 (시간당 제한 체크)
                    dm_sent = False
                    if self.dm_count_this_hour < MAX_DM_PER_HOUR:
                        search_token = product_code or product_name or "해당 상품"
                        bio_text = (
                            f"바이오 링크: {bio_url}"
                            if bio_url else
                            f"바이오 링크에서 {search_token} 검색"
                        )
                        affiliate_text = (
                            f"구매링크: {affiliate_link}"
                            if affiliate_link else ""
                        )
                        dm_text = DM_TEMPLATE.format(
                            product_code=product_code or "N/A",
                            product_name=product_name,
                            bio_text=bio_text,
                            affiliate_text=affiliate_text
                        ).strip()
                        try:
                            user_id = comment.user.pk
                            self.client.direct_send(dm_text, user_ids=[user_id])
                            stats["dms"] += 1
                            self.dm_count_this_hour += 1
                            dm_sent = True
                            logger.info(f"DM 발송 완료: @{comment.user.username}")
                        except Exception as e:
                            logger.warning(f"DM 발송 실패: {e}")

                    # DB 기록
                    interaction_id = insert_interaction(
                        post_id=0,  # 실제로는 posts 테이블의 ID
                        comment_id=comment_id,
                        commenter_username=comment.user.username,
                        comment_text=comment.text
                    )
                    mark_interaction_replied(interaction_id, dm_sent=dm_sent)

                    processed_comment_ids.add(comment_id)

                    # 요청 간 딜레이 (안전)
                    time.sleep(random.uniform(3, 8))

            except Exception as e:
                logger.warning(f"댓글 모니터링 오류: {e}")

            # 폴링 간격
            time.sleep(COMMENT_POLL_INTERVAL)

        logger.info(
            f"댓글 모니터링 종료 - 대댓글: {stats['replies']}개, "
            f"DM: {stats['dms']}개"
        )
        return stats

    # ──────────────────────────────────────────
    # 쿠팡 파트너스 링크 생성
    # ──────────────────────────────────────────

    @staticmethod
    def generate_affiliate_link(product_url: str) -> str:
        """
        쿠팡 파트너스 제휴 링크 생성
        실제 구현 시 쿠팡 파트너스 API를 사용합니다.
        """
        try:
            import hmac
            import hashlib
            from urllib.parse import urlencode, quote

            from config import COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY, COUPANG_PARTNER_ID

            if not all([COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY, COUPANG_PARTNER_ID]):
                logger.warning("쿠팡 파트너스 API 키가 설정되지 않았습니다.")
                return product_url

            # 쿠팡 파트너스 Deep Link API
            # 실제 API 호출 구현
            import requests
            from datetime import datetime

            method = "POST"
            path = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
            datetime_str = datetime.utcnow().strftime("%y%m%dT%H%M%SZ")

            message = datetime_str + method + path
            signature = hmac.new(
                COUPANG_SECRET_KEY.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            authorization = (
                f"CEA algorithm=HmacSHA256, access-key={COUPANG_ACCESS_KEY}, "
                f"signed-date={datetime_str}, signature={signature}"
            )

            url = f"https://api-gateway.coupang.com{path}"
            headers = {
                "Authorization": authorization,
                "Content-Type": "application/json"
            }
            payload = {
                "coupangUrls": [product_url]
            }

            response = requests.post(url, headers=headers, json=payload, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if data.get("data"):
                    short_url = data["data"][0].get("shortenUrl", product_url)
                    logger.info(f"제휴 링크 생성: {short_url}")
                    return short_url

            return product_url

        except Exception as e:
            logger.warning(f"제휴 링크 생성 실패: {e}")
            return product_url


# ──────────────────────────────────────────────
# CLI 실행
# ──────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    manager = InstagramManager()

    if manager.is_logged_in():
        print("인스타그램 로그인 성공!")
        caption, hashtags = manager.generate_caption("AirPods Pro 2")
        print(f"캡션: {caption}")
        print(f"해시태그: {hashtags}")
    else:
        print("인스타그램 로그인 실패 - 자격증명을 확인하세요.")
