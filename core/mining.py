"""
mining.py - 바이럴 영상 마이닝 모듈
TikTok/YouTube Shorts에서 바이럴 영상을 검색하고 yt-dlp로 다운로드합니다.
"""

import json
import logging
import subprocess
import re
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    MIN_VIEW_COUNT, MIN_LIKE_COUNT, MIN_DURATION, MAX_DURATION,
    DOWNLOADS_DIR
)
from core.database import insert_video, is_url_processed

logger = logging.getLogger(__name__)


class VideoMiner:
    """바이럴 영상 검색 및 다운로드 클래스"""

    def __init__(self):
        self.download_dir = DOWNLOADS_DIR / "raw"
        self.download_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────
    # YouTube Shorts 검색
    # ──────────────────────────────────────────

    def search_youtube_shorts(self, keyword: str,
                              max_results: int = 10) -> list[dict]:
        """
        yt-dlp를 사용하여 YouTube Shorts 검색
        Returns: [{"url": str, "title": str, "view_count": int,
                   "like_count": int, "duration": float}, ...]
        """
        logger.info(f"YouTube Shorts 검색: '{keyword}'")
        search_query = f"ytsearch{max_results}:{keyword} shorts"

        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--dump-json",
                    "--flat-playlist",
                    "--no-download",
                    search_query
                ],
                capture_output=True, text=True, timeout=60
            )

            videos = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    video_info = {
                        "url": data.get("url") or f"https://www.youtube.com/watch?v={data.get('id', '')}",
                        "title": data.get("title", ""),
                        "view_count": data.get("view_count", 0) or 0,
                        "like_count": data.get("like_count", 0) or 0,
                        "duration": data.get("duration", 0) or 0,
                        "platform": "youtube",
                    }
                    videos.append(video_info)
                except json.JSONDecodeError:
                    continue

            logger.info(f"YouTube 검색 결과: {len(videos)}개 발견")
            return videos

        except subprocess.TimeoutExpired:
            logger.error("YouTube 검색 타임아웃")
            return []
        except Exception as e:
            logger.error(f"YouTube 검색 실패: {e}")
            return []

    # ──────────────────────────────────────────
    # TikTok 검색
    # ──────────────────────────────────────────

    def search_tiktok(self, keyword: str,
                      max_results: int = 10) -> list[dict]:
        """
        yt-dlp를 사용하여 TikTok 검색
        Returns: [{"url": str, "title": str, "view_count": int,
                   "like_count": int, "duration": float}, ...]
        """
        logger.info(f"TikTok 검색: '{keyword}'")

        try:
            # TikTok 검색 URL 구성
            search_url = f"https://www.tiktok.com/search?q={keyword}"

            result = subprocess.run(
                [
                    "yt-dlp",
                    "--dump-json",
                    "--flat-playlist",
                    "--no-download",
                    "--playlist-items", f"1:{max_results}",
                    search_url
                ],
                capture_output=True, text=True, timeout=60
            )

            videos = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    video_info = {
                        "url": data.get("webpage_url") or data.get("url", ""),
                        "title": data.get("title", data.get("description", "")),
                        "view_count": data.get("view_count", 0) or 0,
                        "like_count": data.get("like_count", 0) or 0,
                        "duration": data.get("duration", 0) or 0,
                        "platform": "tiktok",
                    }
                    videos.append(video_info)
                except json.JSONDecodeError:
                    continue

            logger.info(f"TikTok 검색 결과: {len(videos)}개 발견")
            return videos

        except subprocess.TimeoutExpired:
            logger.error("TikTok 검색 타임아웃")
            return []
        except Exception as e:
            logger.error(f"TikTok 검색 실패: {e}")
            return []

    # ──────────────────────────────────────────
    # 필터링
    # ──────────────────────────────────────────

    def filter_viral_videos(self, videos: list[dict],
                            min_views: int = None,
                            min_likes: int = None,
                            min_dur: int = None,
                            max_dur: int = None) -> list[dict]:
        """
        바이럴 기준에 맞는 영상만 필터링
        - 조회수 >= MIN_VIEW_COUNT
        - 좋아요 >= MIN_LIKE_COUNT
        - 영상 길이: MIN_DURATION ~ MAX_DURATION
        """
        min_views = min_views or MIN_VIEW_COUNT
        min_likes = min_likes or MIN_LIKE_COUNT
        min_dur = min_dur or MIN_DURATION
        max_dur = max_dur or MAX_DURATION

        filtered = []
        for v in videos:
            view_ok = v.get("view_count", 0) >= min_views
            like_ok = v.get("like_count", 0) >= min_likes
            dur = v.get("duration", 0)
            dur_ok = min_dur <= dur <= max_dur if dur > 0 else True

            if view_ok and like_ok and dur_ok:
                filtered.append(v)
                logger.info(
                    f"✓ 필터 통과: {v['title'][:30]}... "
                    f"(조회수:{v['view_count']:,}, 좋아요:{v['like_count']:,}, "
                    f"길이:{v['duration']}초)"
                )
            else:
                logger.debug(
                    f"✗ 필터 미통과: {v['title'][:30]}... "
                    f"(조회수:{v.get('view_count', 0):,}, "
                    f"좋아요:{v.get('like_count', 0):,}, "
                    f"길이:{v.get('duration', 0)}초)"
                )

        logger.info(f"필터링 결과: {len(videos)}개 중 {len(filtered)}개 통과")
        return filtered

    # ──────────────────────────────────────────
    # 다운로드
    # ──────────────────────────────────────────

    def download_video(self, url: str, product_id: int = None,
                       filename: str = None) -> Optional[str]:
        """
        yt-dlp로 영상 다운로드 (워터마크 제거 옵션 포함)
        Returns: 다운로드된 파일 경로 또는 None
        """
        if is_url_processed(url):
            logger.info(f"이미 처리된 URL, 스킵: {url}")
            return None

        if not filename:
            # 안전한 파일명 생성
            safe_name = re.sub(r'[^\w\-]', '_', url.split("/")[-1][:50])
            filename = f"{safe_name}"

        output_path = self.download_dir / f"{filename}.mp4"

        try:
            cmd = [
                "yt-dlp",
                "-f", "best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", str(output_path),
                "--no-playlist",
                "--no-warnings",
                "--quiet",
            ]

            # TikTok 워터마크 제거 시도
            if "tiktok" in url.lower():
                cmd.extend(["--extractor-args", "tiktok:api_hostname=api22-normal-c-useast2a.tiktokv.com"])

            cmd.append(url)

            logger.info(f"영상 다운로드 중: {url}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if output_path.exists():
                logger.info(f"다운로드 완료: {output_path}")
                return str(output_path)
            else:
                # glob으로 실제 파일 찾기
                possible = list(self.download_dir.glob(f"{filename}*"))
                if possible:
                    logger.info(f"다운로드 완료 (대체 경로): {possible[0]}")
                    return str(possible[0])

                logger.error(f"다운로드 실패 - 파일 없음: {result.stderr[:200]}")
                return None

        except subprocess.TimeoutExpired:
            logger.error(f"다운로드 타임아웃: {url}")
            return None
        except Exception as e:
            logger.error(f"다운로드 실패: {e}")
            return None

    # ──────────────────────────────────────────
    # 통합 파이프라인
    # ──────────────────────────────────────────

    def run_mining_pipeline(self, product: dict,
                            max_videos: int = 3) -> list[dict]:
        """
        단일 상품에 대한 영상 마이닝 파이프라인
        1. 키워드로 YouTube + TikTok 검색
        2. 바이럴 기준 필터링
        3. 상위 영상 다운로드
        4. DB 저장

        Args:
            product: {"id": int, "keywords": [str], "name_en": str, ...}
            max_videos: 다운로드할 최대 영상 수

        Returns: 다운로드된 영상 정보 리스트
        """
        product_id = product.get("id")
        keywords = product.get("keywords", [])
        product_name = product.get("name_en", product.get("name", ""))

        if not keywords:
            keywords = [product_name]

        logger.info(f"=== 영상 마이닝 시작: {product_name} ===")
        logger.info(f"검색 키워드: {keywords}")

        all_videos = []

        for keyword in keywords[:3]:  # 최대 3개 키워드
            # YouTube Shorts 검색
            yt_results = self.search_youtube_shorts(keyword)
            all_videos.extend(yt_results)

            # TikTok 검색
            tt_results = self.search_tiktok(keyword)
            all_videos.extend(tt_results)

        # 중복 제거 (URL 기준)
        seen_urls = set()
        unique_videos = []
        for v in all_videos:
            if v["url"] not in seen_urls:
                seen_urls.add(v["url"])
                unique_videos.append(v)

        logger.info(f"총 {len(unique_videos)}개 고유 영상 발견")

        # 필터링
        viral_videos = self.filter_viral_videos(unique_videos)

        # 조회수 기준 정렬
        viral_videos.sort(key=lambda x: x.get("view_count", 0), reverse=True)

        # 상위 영상 다운로드
        downloaded = []
        for video in viral_videos[:max_videos]:
            local_path = self.download_video(
                url=video["url"],
                product_id=product_id,
                filename=f"product_{product_id}_{video['platform']}_{len(downloaded)}"
            )

            if local_path:
                # DB 저장
                video_id = insert_video(
                    product_id=product_id,
                    platform=video["platform"],
                    original_url=video["url"],
                    local_path=local_path,
                    view_count=video.get("view_count", 0),
                    like_count=video.get("like_count", 0),
                    duration=video.get("duration", 0)
                )
                video["id"] = video_id
                video["local_path"] = local_path
                downloaded.append(video)

        logger.info(f"=== 마이닝 완료: {len(downloaded)}개 영상 다운로드 ===")
        return downloaded


# ──────────────────────────────────────────────
# CLI 실행
# ──────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    miner = VideoMiner()

    # 테스트
    sample_product = {
        "id": 1,
        "name": "에어팟 프로 2세대",
        "name_en": "AirPods Pro 2nd Generation",
        "keywords": ["AirPods Pro 2 review", "AirPods Pro unboxing"]
    }
    results = miner.run_mining_pipeline(sample_product)
    for v in results:
        print(f"[{v.get('id')}] {v['platform']}: {v['title'][:40]}...")
        print(f"    Path: {v.get('local_path')}")
