"""
video_hosting.py - Instagram Graph API 업로드용 영상 공개 URL 생성
Cloudinary 업로드 또는 public URL 매핑을 지원합니다.
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    VIDEO_HOSTING,
    VIDEO_PUBLIC_BASE_URL,
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
    CLOUDINARY_FOLDER,
)

logger = logging.getLogger(__name__)


def _require(value: str, label: str):
    if not value:
        raise RuntimeError(f"{label} 설정이 필요합니다.")


def get_public_video_url(video_path: str) -> str:
    """
    Instagram Graph API 업로드를 위해 공개 접근 가능한 video URL을 반환합니다.
    - VIDEO_HOSTING=cloudinary: Cloudinary로 업로드 후 secure_url 반환
    - VIDEO_HOSTING=public_url: VIDEO_PUBLIC_BASE_URL + 파일명
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"영상 파일이 없습니다: {path}")

    mode = (VIDEO_HOSTING or "").lower()
    if mode == "cloudinary":
        _require(CLOUDINARY_CLOUD_NAME, "CLOUDINARY_CLOUD_NAME")
        _require(CLOUDINARY_API_KEY, "CLOUDINARY_API_KEY")
        _require(CLOUDINARY_API_SECRET, "CLOUDINARY_API_SECRET")

        try:
            import cloudinary
            import cloudinary.uploader
        except Exception as e:
            raise RuntimeError("cloudinary 패키지가 설치되지 않았습니다.") from e

        cloudinary.config(
            cloud_name=CLOUDINARY_CLOUD_NAME,
            api_key=CLOUDINARY_API_KEY,
            api_secret=CLOUDINARY_API_SECRET,
            secure=True,
        )

        logger.info("Cloudinary 업로드 시작: %s", path.name)
        result = cloudinary.uploader.upload(
            str(path),
            resource_type="video",
            folder=CLOUDINARY_FOLDER,
            overwrite=True,
            unique_filename=True,
        )
        url = result.get("secure_url") or result.get("url")
        if not url:
            raise RuntimeError("Cloudinary 업로드 결과에 URL이 없습니다.")
        logger.info("Cloudinary 업로드 완료")
        return url

    if mode == "public_url":
        _require(VIDEO_PUBLIC_BASE_URL, "VIDEO_PUBLIC_BASE_URL")
        return f"{VIDEO_PUBLIC_BASE_URL.rstrip('/')}/{path.name}"

    raise RuntimeError(
        "VIDEO_HOSTING 설정이 필요합니다. "
        "cloudinary 또는 public_url 로 설정하세요."
    )
