"""
editing.py - 스마트 영상 편집 모듈
바이럴 공식(미러링, 속도 변경, 크롭, BGM, 후킹 자막)을 적용하여 영상을 재가공합니다.
"""

import json
import logging
import random
from pathlib import Path
from typing import Optional

from openai import OpenAI

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    OPENAI_API_KEY, SPEED_FACTOR, CROP_ZOOM, ORIGINAL_AUDIO_VOLUME,
    HOOK_DURATION, HOOK_FONT_SIZE, HOOK_FONT_COLOR,
    HOOK_TEXT_PROMPT, DOWNLOADS_DIR, ASSETS_DIR
)
from core.database import update_video_edited

logger = logging.getLogger(__name__)


class VideoEditor:
    """바이럴 영상 편집 클래스"""

    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.output_dir = DOWNLOADS_DIR / "edited"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bgm_dir = ASSETS_DIR / "bgm"
        self.bgm_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────
    # GPT 후킹 문구 생성
    # ──────────────────────────────────────────

    def generate_hook_text(self, product_name: str) -> str:
        """GPT로 후킹 문구 생성"""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": HOOK_TEXT_PROMPT.format(product_name=product_name)
                }],
                max_tokens=100,
                temperature=0.9
            )
            hook = response.choices[0].message.content.strip()
            # 따옴표 제거
            hook = hook.strip('"\'')
            logger.info(f"후킹 문구 생성: {hook}")
            return hook
        except Exception as e:
            logger.error(f"후킹 문구 생성 실패: {e}")
            # 기본 후킹 문구
            defaults = [
                "와 이거 실화?! 🤯",
                "이거 안 사면 후회함 ㅋㅋ 🔥",
                "역대급 가성비 발견! 💰",
                "이거 만든 사람 천재 아님? 😱",
            ]
            return random.choice(defaults)

    # ──────────────────────────────────────────
    # 영상 편집 파이프라인
    # ──────────────────────────────────────────

    def edit_video(self, input_path: str, product_name: str = "",
                   video_id: int = None, bgm_path: str = None) -> Optional[str]:
        """
        영상에 바이럴 공식 적용:
        1. 좌우 반전 (Mirroring)
        2. 속도 1.15배 (Speed Up)
        3. 5% 확대 (Crop/Zoom-in)
        4. 원본 오디오 30% + BGM
        5. 후킹 자막 (3초간)

        Args:
            input_path: 원본 영상 파일 경로
            product_name: 상품명 (후킹 문구 생성용)
            video_id: DB 영상 ID
            bgm_path: BGM 파일 경로 (없으면 기본 BGM 사용)

        Returns: 편집된 영상 파일 경로 또는 None
        """
        try:
            from moviepy.editor import (
                VideoFileClip, TextClip, CompositeVideoClip,
                AudioFileClip, CompositeAudioClip, vfx
            )
        except ImportError:
            logger.error("moviepy가 설치되지 않았습니다. pip install moviepy")
            return None

        input_path = Path(input_path)
        if not input_path.exists():
            logger.error(f"입력 파일 없음: {input_path}")
            return None

        output_filename = f"edited_{input_path.stem}.mp4"
        output_path = self.output_dir / output_filename

        try:
            logger.info(f"영상 편집 시작: {input_path.name}")

            # 원본 로드
            clip = VideoFileClip(str(input_path))
            original_duration = clip.duration
            logger.info(f"원본 영상: {original_duration:.1f}초, {clip.size}")

            # ── 1. 좌우 반전 (Mirroring) ──
            clip = clip.fx(vfx.mirror_x)
            logger.info("✓ 좌우 반전 적용")

            # ── 2. 속도 변경 (1.15x) ──
            clip = clip.fx(vfx.speedx, SPEED_FACTOR)
            logger.info(f"✓ 속도 {SPEED_FACTOR}x 적용")

            # ── 3. 확대/크롭 (5% Zoom-in) ──
            w, h = clip.size
            crop_x = int(w * CROP_ZOOM)
            crop_y = int(h * CROP_ZOOM)
            clip = clip.crop(
                x1=crop_x, y1=crop_y,
                x2=w - crop_x, y2=h - crop_y
            ).resize((w, h))  # 원본 해상도로 리사이즈
            logger.info(f"✓ {CROP_ZOOM*100:.0f}% 확대(Zoom-in) 적용")

            # ── 4. 오디오 처리 ──
            if clip.audio:
                # 원본 오디오 볼륨 30%로 감소
                original_audio = clip.audio.volumex(ORIGINAL_AUDIO_VOLUME)

                # BGM 추가
                bgm_audio = self._load_bgm(bgm_path, clip.duration)
                if bgm_audio:
                    final_audio = CompositeAudioClip([original_audio, bgm_audio])
                    clip = clip.set_audio(final_audio)
                    logger.info("✓ BGM 합성 완료")
                else:
                    clip = clip.set_audio(original_audio)
                    logger.info("✓ 원본 오디오 볼륨 조정 완료 (BGM 없음)")
            else:
                logger.info("원본 오디오 없음, BGM만 적용 시도")
                bgm_audio = self._load_bgm(bgm_path, clip.duration)
                if bgm_audio:
                    clip = clip.set_audio(bgm_audio)

            # ── 5. 후킹 자막 (3초간) ──
            if product_name:
                hook_text = self.generate_hook_text(product_name)
            else:
                hook_text = "이거 실화?! 🤯"

            try:
                txt_clip = (
                    TextClip(
                        hook_text,
                        fontsize=HOOK_FONT_SIZE,
                        color=HOOK_FONT_COLOR,
                        font="NanumGothic-Bold",
                        stroke_color="black",
                        stroke_width=2,
                        method="caption",
                        size=(w * 0.8, None),
                        align="center"
                    )
                    .set_position("center")
                    .set_start(0)
                    .set_duration(min(HOOK_DURATION, clip.duration))
                    .crossfadein(0.3)
                    .crossfadeout(0.3)
                )
                clip = CompositeVideoClip([clip, txt_clip])
                logger.info(f"✓ 후킹 자막 적용: '{hook_text}'")
            except Exception as e:
                logger.warning(f"자막 적용 실패 (폰트 문제 가능): {e}")
                # 자막 없이 계속 진행

            # ── 출력 ──
            clip.write_videofile(
                str(output_path),
                codec="libx264",
                audio_codec="aac",
                fps=30,
                preset="medium",
                threads=4,
                logger=None  # moviepy 로그 억제
            )

            # 리소스 해제
            clip.close()

            if output_path.exists():
                logger.info(f"편집 완료: {output_path}")

                # DB 업데이트
                if video_id:
                    update_video_edited(video_id, str(output_path))

                return str(output_path)
            else:
                logger.error("편집 파일 생성 실패")
                return None

        except Exception as e:
            logger.error(f"영상 편집 실패: {e}")
            return None

    def _load_bgm(self, bgm_path: str = None,
                   target_duration: float = 30) -> Optional[object]:
        """BGM 오디오 로드 및 길이 조정"""
        try:
            from moviepy.editor import AudioFileClip

            if bgm_path and Path(bgm_path).exists():
                bgm = AudioFileClip(bgm_path)
            else:
                # assets/bgm 폴더에서 랜덤 BGM 선택
                bgm_files = list(self.bgm_dir.glob("*.mp3"))
                if not bgm_files:
                    logger.info("BGM 파일 없음 - assets/bgm/ 폴더에 mp3 파일을 추가하세요")
                    return None
                bgm = AudioFileClip(str(random.choice(bgm_files)))

            # 영상 길이에 맞춰 BGM 자르기
            if bgm.duration > target_duration:
                bgm = bgm.subclip(0, target_duration)
            elif bgm.duration < target_duration:
                # BGM이 짧으면 루프
                from moviepy.editor import concatenate_audioclips
                loops = int(target_duration / bgm.duration) + 1
                bgm = concatenate_audioclips([bgm] * loops).subclip(0, target_duration)

            # BGM 볼륨 (원본보다 약간 낮게)
            bgm = bgm.volumex(0.25)
            return bgm

        except Exception as e:
            logger.warning(f"BGM 로드 실패: {e}")
            return None

    # ──────────────────────────────────────────
    # 배치 편집
    # ──────────────────────────────────────────

    def batch_edit(self, videos: list[dict],
                   product_name: str = "") -> list[dict]:
        """
        여러 영상을 일괄 편집
        Args:
            videos: [{"id": int, "local_path": str, ...}, ...]
            product_name: 상품명
        Returns: 편집 완료된 영상 정보 리스트
        """
        edited_videos = []

        for video in videos:
            edited_path = self.edit_video(
                input_path=video["local_path"],
                product_name=product_name,
                video_id=video.get("id")
            )

            if edited_path:
                video["edited_path"] = edited_path
                edited_videos.append(video)

        logger.info(f"배치 편집 완료: {len(edited_videos)}/{len(videos)}개 성공")
        return edited_videos


# ──────────────────────────────────────────────
# CLI 실행
# ──────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    editor = VideoEditor()

    # 테스트: 후킹 문구 생성
    hook = editor.generate_hook_text("AirPods Pro 2nd Generation")
    print(f"후킹 문구: {hook}")
