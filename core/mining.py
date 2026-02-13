"""
mining.py - 바이럴 영상 마이닝 모듈
TikTok/YouTube Shorts에서 바이럴 영상을 검색하고 yt-dlp로 다운로드합니다.
"""

import json
import logging
import subprocess
import re
import requests
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    MIN_VIEW_COUNT, MIN_LIKE_COUNT, MIN_DURATION, MAX_DURATION,
    DOWNLOADS_DIR,
    IG_GRAPH_API_VERSION, IG_GRAPH_HOST, IG_USER_ID, IG_ACCESS_TOKEN,
    IG_MINING_ENABLED, IG_MINING_TOP_MEDIA, IG_MINING_MAX_RESULTS,
)
from core.database import insert_video, is_url_processed

logger = logging.getLogger(__name__)


class VideoMiner:
    """바이럴 영상 검색 및 다운로드 클래스"""

    def __init__(self):
        self.download_dir = DOWNLOADS_DIR / "raw"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.keyword_suffixes = ["review", "unboxing", "how to use"]
        self.stopwords = set([
            "for", "with", "and", "or", "the", "a", "an", "of", "to",
            "set", "sets", "pack", "pcs", "pc", "piece", "pieces",
            "new", "hot", "best", "top", "sale", "fashion", "casual",
            "women", "woman", "men", "man", "kids", "girls", "boys",
            "세트", "포함", "남성", "여성", "용", "및",
        ])

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
    # Instagram Reels 검색 (Graph API)
    # ──────────────────────────────────────────

    def search_instagram_reels(self, keyword: str,
                               max_results: int = None) -> list[dict]:
        """
        Instagram Graph API 해시태그 검색으로 릴스/비디오 수집
        Returns: [{"url": str, "title": str, "platform": "instagram", ...}, ...]
        """
        max_results = max_results or IG_MINING_MAX_RESULTS
        if not IG_MINING_ENABLED:
            return []
        if not (IG_USER_ID and IG_ACCESS_TOKEN):
            logger.debug("IG_MINING: 토큰 또는 IG_USER_ID 미설정")
            return []
        if not keyword:
            return []

        hashtags = self._build_hashtags(keyword)
        videos: list[dict] = []

        for tag in hashtags:
            hashtag_id = self._ig_hashtag_search(tag)
            if not hashtag_id:
                continue
            media = self._ig_hashtag_media(hashtag_id, max_results=max_results)
            for m in media:
                media_type = (m.get("media_type") or "").upper()
                product_type = (m.get("media_product_type") or "").upper()
                if media_type not in ("VIDEO", "REELS") and product_type != "REELS":
                    continue
                media_url = m.get("media_url")
                if not media_url:
                    continue
                videos.append({
                    "url": media_url,
                    "dedupe_url": m.get("permalink") or m.get("id") or media_url,
                    "title": (m.get("caption") or tag)[:120],
                    "view_count": 0,
                    "like_count": 0,
                    "duration": 0,
                    "platform": "instagram",
                })

            if len(videos) >= max_results:
                break

        logger.info(f"Instagram Reels 검색 결과: {len(videos)}개 발견")
        return videos[:max_results]

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
                       filename: str = None, dedupe_url: str = None) -> Optional[str]:
        """
        yt-dlp로 영상 다운로드 (워터마크 제거 옵션 포함)
        Returns: 다운로드된 파일 경로 또는 None
        """
        if is_url_processed(dedupe_url or url):
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

        search_keywords = self._build_search_keywords(keywords, product_name)
        logger.info(f"=== 영상 마이닝 시작: {product_name} ===")
        logger.info(f"검색 키워드(단순화): {search_keywords}")

        all_videos = []

        for keyword in search_keywords[:6]:  # 최대 6개 키워드
            # YouTube Shorts 검색
            yt_results = self.search_youtube_shorts(keyword)
            all_videos.extend(yt_results)

            # TikTok 검색
            tt_results = self.search_tiktok(keyword)
            all_videos.extend(tt_results)

            # Instagram Reels 검색
            ig_results = self.search_instagram_reels(keyword)
            all_videos.extend(ig_results)

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
                filename=f"product_{product_id}_{video['platform']}_{len(downloaded)}",
                dedupe_url=video.get("dedupe_url")
            )

            if local_path:
                # DB 저장
                video_id = insert_video(
                    product_id=product_id,
                    platform=video["platform"],
                    original_url=video.get("dedupe_url") or video["url"],
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

    def _ig_request(self, path: str, params: dict | None = None) -> dict:
        params = params or {}
        params.setdefault("access_token", IG_ACCESS_TOKEN)
        url = f"https://{IG_GRAPH_HOST}/{IG_GRAPH_API_VERSION}/{path.lstrip('/')}"
        resp = requests.get(url, params=params, timeout=20)
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
            logger.debug(f"IG Graph API 오류: {data}")
            return {}
        return data if isinstance(data, dict) else {}

    def _ig_hashtag_search(self, hashtag: str) -> str:
        data = self._ig_request(
            "ig_hashtag_search",
            params={"user_id": IG_USER_ID, "q": hashtag}
        )
        items = data.get("data") if isinstance(data, dict) else None
        if not items:
            return ""
        return items[0].get("id", "")

    def _ig_hashtag_media(self, hashtag_id: str, max_results: int = 6) -> list[dict]:
        if not hashtag_id:
            return []
        edge = "top_media" if IG_MINING_TOP_MEDIA else "recent_media"
        fields = "id,caption,media_type,media_product_type,media_url,permalink"
        data = self._ig_request(
            f"{hashtag_id}/{edge}",
            params={
                "user_id": IG_USER_ID,
                "fields": fields,
                "limit": str(max_results)
            }
        )
        return data.get("data", []) if isinstance(data, dict) else []

    def _build_hashtags(self, keyword: str) -> list[str]:
        """키워드를 해시태그 후보로 변환"""
        cleaned = re.sub(r"[^0-9a-zA-Z가-힣 ]+", " ", keyword).strip()
        if not cleaned:
            return []
        tokens = [t for t in cleaned.split() if t]
        tags = []
        if tokens:
            tags.append("".join(tokens))
            if len(tokens) >= 2:
                tags.append("".join(tokens[:2]))
            if len(tokens) >= 1:
                tags.append(tokens[0])
        # 중복 제거
        seen = set()
        result = []
        for t in tags:
            tl = t.lower()
            if tl in seen:
                continue
            seen.add(tl)
            result.append(t)
        return result

    def mine_by_keyword(self, keyword: str,
                        max_videos: int = 5) -> list[dict]:
        """
        키워드 기반으로 리뷰/언박싱 영상 수집
        Returns: 다운로드된 영상 리스트
        """
        if not keyword:
            return []

        pseudo_product = {
            "id": None,
            "name_en": keyword,
            "keywords": [keyword],
        }
        return self.run_mining_pipeline(pseudo_product, max_videos=max_videos)

    def infer_product_query(self, videos: list[dict], fallback: str = "") -> str:
        """영상 제목에서 상품 검색어 추정"""
        titles = [v.get("title", "") for v in videos if v.get("title")]
        for title in titles[:5]:
            candidate = self._normalize_keyword(title)
            if candidate:
                return candidate
        return fallback

    def _build_search_keywords(self, keywords: list, product_name: str) -> list[str]:
        bases = []
        for kw in keywords[:5]:
            base = self._normalize_keyword(str(kw))
            if base:
                bases.append(base)

        # 제품명 기반 폴백
        name_base = self._normalize_keyword(product_name)
        if name_base:
            bases.append(name_base)

        # 중복 제거
        seen = set()
        unique_bases = []
        for b in bases:
            key = b.lower()
            if key in seen:
                continue
            seen.add(key)
            unique_bases.append(b)

        # 너무 길지 않게 2~3개만 사용
        unique_bases = unique_bases[:3] if unique_bases else []

        search_keywords = []
        for base in unique_bases:
            search_keywords.append(base)
            for suffix in self.keyword_suffixes:
                search_keywords.append(f"{base} {suffix}")

        # 최종 중복 제거
        final = []
        seen = set()
        for k in search_keywords:
            key = k.lower()
            if key in seen:
                continue
            seen.add(key)
            final.append(k)
        return final

    def _normalize_keyword(self, text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"[^0-9a-zA-Z가-힣 ]+", " ", text)
        tokens = []
        for raw in cleaned.split():
            t = raw.strip()
            if not t:
                continue
            lower = t.lower()
            if lower in self.stopwords:
                continue
            if lower.isdigit():
                continue
            if re.fullmatch(r"\d+(cm|mm|m|l|ml|kg|g|oz|in|inch)", lower):
                continue
            tokens.append(t)

        # 중복 제거 + 최대 4단어
        seen = set()
        result = []
        for t in tokens:
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(t)
            if len(result) >= 4:
                break
        return " ".join(result).strip()


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
