"""
bot.py - Telegram 봇 제어 및 알림 모듈
작업 상태 확인, 강제 실행, 실시간 알림을 처리합니다.
"""

import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.database import get_stats, get_recent_logs

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram 알림 발송 클래스 (단방향)"""

    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            logger.warning("Telegram 봇 토큰 또는 Chat ID가 설정되지 않았습니다.")

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Telegram 메시지 발송"""
        if not self.enabled:
            logger.info(f"[Telegram 비활성] {text[:50]}...")
            return False

        try:
            import requests

            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                logger.info("Telegram 메시지 발송 성공")
                return True
            else:
                logger.error(f"Telegram 발송 실패: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Telegram 발송 오류: {e}")
            return False

    def notify_start(self):
        """프로세스 시작 알림"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.send_message(
            f"🚀 <b>자동화 봇 시작</b>\n"
            f"📅 {now}\n"
            f"상품 소싱 → 영상 제작 → 업로드 프로세스를 시작합니다."
        )

    def notify_product_sourced(self, product_name: str, keywords: list):
        """상품 소싱 완료 알림"""
        kw_text = ", ".join(keywords[:3]) if keywords else "N/A"
        self.send_message(
            f"📦 <b>상품 소싱 완료</b>\n"
            f"상품: {product_name}\n"
            f"키워드: {kw_text}"
        )

    def notify_video_created(self, product_name: str, video_count: int):
        """영상 제작 완료 알림"""
        self.send_message(
            f"🎬 <b>영상 제작 완료</b>\n"
            f"상품: {product_name}\n"
            f"제작 영상: {video_count}개"
        )

    def notify_upload_success(self, product_name: str, media_id: str):
        """업로드 성공 알림"""
        self.send_message(
            f"✅ <b>업로드 성공</b>\n"
            f"상품: {product_name}\n"
            f"Media ID: {media_id}\n"
            f"인스타그램 릴스에 게시되었습니다!"
        )

    def notify_engagement(self, product_name: str, replies: int, dms: int):
        """댓글 처리 알림"""
        self.send_message(
            f"💬 <b>댓글 처리 완료</b>\n"
            f"상품: {product_name}\n"
            f"대댓글: {replies}개\n"
            f"DM 발송: {dms}개"
        )

    def notify_error(self, error_message: str):
        """에러 알림"""
        self.send_message(
            f"❌ <b>오류 발생</b>\n"
            f"{error_message[:500]}"
        )

    def notify_complete(self, stats: dict):
        """프로세스 완료 알림"""
        self.send_message(
            f"🏁 <b>자동화 프로세스 완료</b>\n\n"
            f"📊 이번 실행 결과:\n"
            f"  • 처리 상품: {stats.get('products', 0)}개\n"
            f"  • 제작 영상: {stats.get('videos', 0)}개\n"
            f"  • 업로드: {stats.get('posts', 0)}개\n"
            f"  • DM 발송: {stats.get('dms', 0)}개"
        )

    def send_status(self) -> str:
        """현재 상태 조회 및 발송"""
        stats = get_stats()
        recent = get_recent_logs(limit=3)

        status_text = (
            f"📊 <b>봇 상태 리포트</b>\n\n"
            f"<b>전체 통계:</b>\n"
            f"  • 총 상품: {stats['total_products']}개\n"
            f"  • 총 영상: {stats['total_videos']}개\n"
            f"  • 총 게시물: {stats['total_posts']}개\n"
            f"  • 총 상호작용: {stats['total_interactions']}개\n"
            f"  • 총 DM: {stats['total_dms']}개\n\n"
            f"<b>최근 실행 기록:</b>\n"
        )

        for log in recent:
            status_text += (
                f"  [{log.get('run_type', 'N/A')}] "
                f"{log.get('started_at', 'N/A')[:16]} - "
                f"{log.get('status', 'N/A')}\n"
            )

        self.send_message(status_text)
        return status_text


class TelegramBotHandler:
    """Telegram 봇 명령 처리 클래스 (양방향)"""

    def __init__(self, pipeline_callback=None):
        """
        Args:
            pipeline_callback: /force_start 시 호출할 파이프라인 함수
        """
        self.token = TELEGRAM_BOT_TOKEN
        self.notifier = TelegramNotifier()
        self.pipeline_callback = pipeline_callback
        self._running = False

    async def start_polling(self):
        """Telegram 봇 폴링 시작"""
        if not self.token:
            logger.warning("Telegram 봇 토큰이 없어 폴링을 시작할 수 없습니다.")
            return

        try:
            from telegram import Update, Bot
            from telegram.ext import (
                Application, CommandHandler, ContextTypes
            )

            app = Application.builder().token(self.token).build()

            # 명령어 핸들러 등록
            app.add_handler(CommandHandler("status", self._cmd_status))
            app.add_handler(CommandHandler("force_start", self._cmd_force_start))
            app.add_handler(CommandHandler("stats", self._cmd_stats))
            app.add_handler(CommandHandler("help", self._cmd_help))
            app.add_handler(CommandHandler("start", self._cmd_help))

            self._running = True
            logger.info("Telegram 봇 폴링 시작")

            await app.initialize()
            await app.start()
            await app.updater.start_polling()

            # 무한 대기
            while self._running:
                await asyncio.sleep(1)

            await app.updater.stop()
            await app.stop()
            await app.shutdown()

        except ImportError:
            logger.error("python-telegram-bot이 설치되지 않았습니다.")
            # 폴백: requests 기반 간단한 폴링
            await self._simple_polling()

    async def _simple_polling(self):
        """python-telegram-bot 없이 requests 기반 간단한 폴링"""
        import requests

        offset = 0
        logger.info("간단한 Telegram 폴링 시작 (requests 기반)")

        while self._running:
            try:
                url = f"https://api.telegram.org/bot{self.token}/getUpdates"
                params = {"offset": offset, "timeout": 30}
                response = requests.get(url, params=params, timeout=35)

                if response.status_code == 200:
                    data = response.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        message = update.get("message", {})
                        text = message.get("text", "")

                        if text == "/status":
                            self.notifier.send_status()
                        elif text == "/force_start":
                            self.notifier.send_message("🚀 강제 실행을 시작합니다...")
                            if self.pipeline_callback:
                                try:
                                    await self.pipeline_callback()
                                except Exception as e:
                                    self.notifier.notify_error(str(e))
                        elif text == "/stats":
                            stats = get_stats()
                            self.notifier.send_message(
                                f"📊 통계\n"
                                f"상품: {stats['total_products']}\n"
                                f"영상: {stats['total_videos']}\n"
                                f"게시물: {stats['total_posts']}\n"
                                f"DM: {stats['total_dms']}"
                            )
                        elif text in ("/help", "/start"):
                            self.notifier.send_message(
                                "🤖 <b>SNS 마케팅 봇 명령어</b>\n\n"
                                "/status - 현재 상태 확인\n"
                                "/force_start - 강제 실행\n"
                                "/stats - 전체 통계\n"
                                "/help - 도움말"
                            )

            except Exception as e:
                logger.error(f"폴링 오류: {e}")
                await asyncio.sleep(5)

            await asyncio.sleep(1)

    def stop(self):
        """봇 폴링 중지"""
        self._running = False

    # ──────────────────────────────────────────
    # 명령어 핸들러 (python-telegram-bot용)
    # ──────────────────────────────────────────

    async def _cmd_status(self, update, context):
        """현재 상태 확인"""
        status = self.notifier.send_status()
        # 직접 응답도 보냄
        await update.message.reply_text(status, parse_mode="HTML")

    async def _cmd_force_start(self, update, context):
        """강제 실행"""
        await update.message.reply_text("🚀 강제 실행을 시작합니다...")
        if self.pipeline_callback:
            try:
                await self.pipeline_callback()
                await update.message.reply_text("✅ 실행 완료!")
            except Exception as e:
                await update.message.reply_text(f"❌ 오류: {e}")
        else:
            await update.message.reply_text("⚠️ 파이프라인이 연결되지 않았습니다.")

    async def _cmd_stats(self, update, context):
        """전체 통계"""
        stats = get_stats()
        text = (
            f"📊 <b>전체 통계</b>\n\n"
            f"상품: {stats['total_products']}개\n"
            f"영상: {stats['total_videos']}개\n"
            f"게시물: {stats['total_posts']}개\n"
            f"상호작용: {stats['total_interactions']}개\n"
            f"DM: {stats['total_dms']}개"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_help(self, update, context):
        """도움말"""
        text = (
            "🤖 <b>SNS 마케팅 자동화 봇</b>\n\n"
            "사용 가능한 명령어:\n"
            "/status - 현재 작업 진행 상황 확인\n"
            "/force_start - 프로세스 즉시 강제 시작\n"
            "/stats - 전체 통계 조회\n"
            "/help - 이 도움말 표시"
        )
        await update.message.reply_text(text, parse_mode="HTML")


# ──────────────────────────────────────────────
# CLI 실행
# ──────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    notifier = TelegramNotifier()
    notifier.send_message("🧪 테스트 메시지입니다!")
    notifier.send_status()
