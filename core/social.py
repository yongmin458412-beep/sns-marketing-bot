"""
social.py - 소셜 미디어 자동화 모듈
인스타그램 릴스 업로드, 댓글 모니터링, 대댓글 + DM 발송을 처리합니다.
"""

import json
import logging
import random
import time
import requests
from pathlib import Path
from typing import Optional

from openai import OpenAI

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    OPENAI_API_KEY, INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD,
    INSTAGRAM_SESSION_FILE, CAPTION_PROMPT, REPLY_TEMPLATES,
    DM_TEMPLATE, COMMENT_POLL_INTERVAL, MAX_DM_PER_HOUR,
    SESSIONS_DIR,
    IG_API_MODE, IG_GRAPH_API_VERSION, IG_GRAPH_HOST, IG_MESSAGE_HOST,
    IG_USER_ID, IG_ACCESS_TOKEN, IG_SHARE_TO_FEED,
    IG_CONTAINER_POLL_INTERVAL, IG_CONTAINER_POLL_TIMEOUT,
)
from core.database import (
    insert_post, insert_interaction, mark_interaction_replied,
    is_comment_processed,
)
from core.video_hosting import get_public_video_url

logger = logging.getLogger(__name__)


class InstagramGraphAPI:
    """Instagram Graph API 클라이언트"""

    def __init__(self):
        self.api_version = IG_GRAPH_API_VERSION
        self.graph_host = IG_GRAPH_HOST
        self.message_host = IG_MESSAGE_HOST
        self.ig_user_id = IG_USER_ID
        self.access_token = IG_ACCESS_TOKEN
        self.share_to_feed = IG_SHARE_TO_FEED
        self.poll_interval = IG_CONTAINER_POLL_INTERVAL
        self.poll_timeout = IG_CONTAINER_POLL_TIMEOUT

    def is_ready(self) -> bool:
        return bool(self.ig_user_id and self.access_token)

    def _request(self, method: str, path: str,
                 params: dict = None,
                 data: dict = None,
                 json_body: dict = None,
                 host: str = None) -> dict:
        url = f"https://{host or self.graph_host}/{self.api_version}/{path.lstrip('/')}"
        params = params or {}
        params.setdefault("access_token", self.access_token)

        response = requests.request(
            method=method,
            url=url,
            params=params,
            data=data,
            json=json_body,
            timeout=30
        )

        try:
            payload = response.json()
        except Exception:
            payload = {}

        if response.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
            error_msg = ""
            if isinstance(payload, dict) and payload.get("error"):
                error_msg = payload["error"].get("message", "")
            raise RuntimeError(
                f"Graph API 오류 ({response.status_code}): {error_msg or response.text}"
            )

        return payload if isinstance(payload, dict) else {}

    def _poll_container(self, container_id: str):
        start = time.time()
        while time.time() - start < self.poll_timeout:
            status = self._request(
                "GET",
                container_id,
                params={"fields": "status_code"}
            )
            status_code = status.get("status_code")
            if status_code == "FINISHED":
                return
            if status_code == "ERROR":
                raise RuntimeError("컨테이너 처리 실패 (status_code=ERROR)")
            time.sleep(self.poll_interval)
        raise RuntimeError("컨테이너 처리 시간 초과")

    def upload_reel(self, video_url: str, caption: str) -> Optional[str]:
        if not self.is_ready():
            raise RuntimeError("Graph API 설정이 없습니다.")

        create = self._request(
            "POST",
            f"{self.ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true" if self.share_to_feed else "false",
            },
        )
        container_id = create.get("id")
        if not container_id:
            raise RuntimeError("미디어 컨테이너 생성 실패")

        self._poll_container(container_id)

        publish = self._request(
            "POST",
            f"{self.ig_user_id}/media_publish",
            data={"creation_id": container_id},
        )
        media_id = publish.get("id")
        return str(media_id) if media_id else None

    def get_comments(self, media_id: str, limit: int = 50) -> list:
        response = self._request(
            "GET",
            f"{media_id}/comments",
            params={
                "fields": "id,text,username,timestamp",
                "limit": str(limit)
            }
        )
        return response.get("data", []) if isinstance(response, dict) else []

    def reply_comment(self, comment_id: str, message: str):
        self._request(
            "POST",
            f"{comment_id}/replies",
            data={"message": message}
        )

    def send_private_reply(self, comment_id: str, message: str):
        self._request(
            "POST",
            f"{self.ig_user_id}/messages",
            json_body={
                "recipient": {"comment_id": comment_id},
                "message": {"text": message},
            },
            host=self.message_host,
        )


class InstagramManager:
    """인스타그램 자동화 관리 클래스"""

    def __init__(self):
        self.mode = (IG_API_MODE or "instagrapi").lower()
        self.client = None
        self.graph = None
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
        self.dm_count_this_hour = 0
        if self.mode == "graph":
            self.graph = InstagramGraphAPI()
        elif self.mode == "instagrapi":
            self._login()
        else:
            logger.warning("IG_API_MODE가 disabled 입니다.")

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
        if self.mode == "graph":
            return self.graph is not None and self.graph.is_ready()
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
            if not self.openai_client:
                raise RuntimeError("OpenAI API 키 미설정")
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
                    caption: str = None, hashtags: str = None,
                    product_code: str = "") -> Optional[str]:
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
        if self.mode == "graph":
            return self._upload_reel_graph(
                video_path=video_path,
                product_name=product_name,
                product_id=product_id,
                video_id=video_id,
                caption=caption,
                hashtags=hashtags,
                product_code=product_code,
            )

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

            code_line = f"코드: {product_code}" if product_code else ""
            full_caption = f"{caption}\n\n{hashtags}"
            if code_line:
                full_caption = f"{full_caption}\n\n{code_line}"

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

    def _upload_reel_graph(self, video_path: str, product_name: str = "",
                           product_id: int = None, video_id: int = None,
                           caption: str = None, hashtags: str = None,
                           product_code: str = "") -> Optional[str]:
        """Instagram Graph API로 릴스 업로드"""
        if not self.graph or not self.graph.is_ready():
            logger.error("Instagram Graph API 설정이 없습니다.")
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

            code_line = f"코드: {product_code}" if product_code else ""
            full_caption = f"{caption}\n\n{hashtags}"
            if code_line:
                full_caption = f"{full_caption}\n\n{code_line}"

            logger.info(f"Graph API 릴스 업로드 시작: {video_path.name}")

            # 공개 URL 생성 (Cloudinary 또는 public URL)
            video_url = get_public_video_url(str(video_path))
            media_id = self.graph.upload_reel(video_url=video_url, caption=full_caption)

            if not media_id:
                logger.error("Graph API 업로드 실패: media_id 없음")
                return None

            logger.info(f"Graph API 릴스 업로드 성공! Media ID: {media_id}")

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
            logger.error(f"Graph API 릴스 업로드 실패: {e}")
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
        if self.mode == "graph":
            return self._monitor_comments_graph(
                media_id=media_id,
                product_name=product_name,
                product_code=product_code,
                affiliate_link=affiliate_link,
                bio_url=bio_url,
                duration_minutes=duration_minutes,
            )

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

    def _monitor_comments_graph(self, media_id: str, product_name: str = "",
                                product_code: str = "",
                                affiliate_link: str = "",
                                bio_url: str = "",
                                duration_minutes: int = 60) -> dict:
        """Graph API 댓글 모니터링 및 자동 응답"""
        if not self.graph or not self.graph.is_ready():
            logger.error("Instagram Graph API 설정이 없습니다.")
            return {"replies": 0, "dms": 0}

        stats = {"replies": 0, "dms": 0}
        processed_comment_ids = set()
        end_time = time.time() + (duration_minutes * 60)

        logger.info(f"Graph API 댓글 모니터링 시작 (Media: {media_id})")

        while time.time() < end_time:
            try:
                comments = self.graph.get_comments(media_id, limit=50)

                for comment in comments:
                    comment_id = str(comment.get("id", "")).strip()
                    if not comment_id:
                        continue

                    if comment_id in processed_comment_ids or is_comment_processed(comment_id):
                        continue

                    username = comment.get("username") or "unknown"
                    comment_text = comment.get("text") or ""

                    # 자기 댓글은 스킵
                    if INSTAGRAM_USERNAME and username == INSTAGRAM_USERNAME:
                        processed_comment_ids.add(comment_id)
                        continue

                    logger.info(
                        f"새 댓글 발견(Graph): @{username}: "
                        f"{comment_text[:50]}..."
                    )

                    # 1. 대댓글 달기
                    reply_text = random.choice(REPLY_TEMPLATES)
                    try:
                        self.graph.reply_comment(comment_id, reply_text)
                        stats["replies"] += 1
                        logger.info(f"대댓글 완료(Graph): '{reply_text}'")
                    except Exception as e:
                        logger.warning(f"대댓글 실패(Graph): {e}")

                    # 2. Private Reply (DM)
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
                            self.graph.send_private_reply(comment_id, dm_text)
                            stats["dms"] += 1
                            self.dm_count_this_hour += 1
                            dm_sent = True
                            logger.info(f"Private Reply 완료(Graph): @{username}")
                        except Exception as e:
                            logger.warning(f"Private Reply 실패(Graph): {e}")

                    interaction_id = insert_interaction(
                        post_id=0,
                        comment_id=comment_id,
                        commenter_username=username,
                        comment_text=comment_text
                    )
                    mark_interaction_replied(interaction_id, dm_sent=dm_sent)
                    processed_comment_ids.add(comment_id)

                    time.sleep(random.uniform(2, 6))

            except Exception as e:
                logger.warning(f"Graph 댓글 모니터링 오류: {e}")

            time.sleep(COMMENT_POLL_INTERVAL)

        logger.info(
            f"Graph 댓글 모니터링 종료 - 대댓글: {stats['replies']}개, "
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
