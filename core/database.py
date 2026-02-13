"""
database.py - SQLite 데이터베이스 관리 모듈
상품, 영상, 게시물, 댓글 처리 이력을 추적합니다.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """SQLite 연결 반환 (자동 테이블 생성 포함)"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection):
    """필요한 테이블 자동 생성"""
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_en TEXT,
            keywords TEXT,
            image_url TEXT,
            price TEXT,
            affiliate_link TEXT,
            source TEXT DEFAULT 'coupang',
            product_code TEXT,
            cta_keyword TEXT,
            linktree_url TEXT,
            notion_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'sourced'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            platform TEXT,
            original_url TEXT,
            local_path TEXT,
            edited_path TEXT,
            view_count INTEGER,
            like_count INTEGER,
            duration REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'downloaded',
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            video_id INTEGER,
            platform TEXT DEFAULT 'instagram',
            post_id TEXT,
            caption TEXT,
            hashtags TEXT,
            upload_time TIMESTAMP,
            status TEXT DEFAULT 'uploaded',
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (video_id) REFERENCES videos(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            comment_id TEXT,
            commenter_username TEXT,
            comment_text TEXT,
            reply_sent INTEGER DEFAULT 0,
            dm_sent INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES posts(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS run_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            products_processed INTEGER DEFAULT 0,
            videos_created INTEGER DEFAULT 0,
            posts_uploaded INTEGER DEFAULT 0,
            dms_sent INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error_message TEXT
        )
    """)

    conn.commit()

    _ensure_columns(conn, "products", {
        "product_code": "TEXT",
        "cta_keyword": "TEXT",
        "linktree_url": "TEXT",
        "notion_url": "TEXT",
    })


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict):
    """테이블에 누락된 컬럼 추가"""
    existing = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    for col, col_def in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
    conn.commit()


# ──────────────────────────────────────────────
# CRUD 함수들
# ──────────────────────────────────────────────

def insert_product(name: str, name_en: str = "", keywords: list = None,
                   image_url: str = "", price: str = "",
                   affiliate_link: str = "", source: str = "coupang",
                   product_code: str = "", cta_keyword: str = "",
                   linktree_url: str = "",
                   notion_url: str = "") -> int:
    """상품 정보 저장 후 ID 반환"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO products (name, name_en, keywords, image_url, price,
           affiliate_link, source, product_code, cta_keyword, linktree_url, notion_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, name_en, json.dumps(keywords or []), image_url, price,
         affiliate_link, source, product_code, cta_keyword, linktree_url, notion_url)
    )
    conn.commit()
    product_id = cursor.lastrowid
    conn.close()
    return product_id


def update_product_code(product_id: int, product_code: str):
    conn = get_connection()
    conn.execute(
        "UPDATE products SET product_code = ? WHERE id = ?",
        (product_code, product_id)
    )
    conn.commit()
    conn.close()


def update_product_affiliate_link(product_id: int, affiliate_link: str):
    conn = get_connection()
    conn.execute(
        "UPDATE products SET affiliate_link = ? WHERE id = ?",
        (affiliate_link, product_id)
    )
    conn.commit()
    conn.close()


def update_product_linktree(product_id: int, linktree_url: str):
    conn = get_connection()
    conn.execute(
        "UPDATE products SET linktree_url = ? WHERE id = ?",
        (linktree_url, product_id)
    )
    conn.commit()
    conn.close()


def update_product_notion(product_id: int, notion_url: str):
    conn = get_connection()
    conn.execute(
        "UPDATE products SET notion_url = ? WHERE id = ?",
        (notion_url, product_id)
    )
    conn.commit()
    conn.close()


def get_today_product_count() -> int:
    """오늘 생성된 상품 수 조회 (로컬타임 기준)"""
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM products "
        "WHERE date(created_at, 'localtime') = date('now', 'localtime')"
    ).fetchone()[0]
    conn.close()
    return count


def insert_video(product_id: int, platform: str, original_url: str,
                 local_path: str = "", view_count: int = 0,
                 like_count: int = 0, duration: float = 0) -> int:
    """영상 정보 저장 후 ID 반환"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO videos (product_id, platform, original_url, local_path,
           view_count, like_count, duration)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (product_id, platform, original_url, local_path, view_count, like_count, duration)
    )
    conn.commit()
    video_id = cursor.lastrowid
    conn.close()
    return video_id


def update_video_product(video_id: int, product_id: int):
    """영상에 상품 ID 연결"""
    conn = get_connection()
    conn.execute(
        "UPDATE videos SET product_id = ? WHERE id = ?",
        (product_id, video_id)
    )
    conn.commit()
    conn.close()


def update_video_edited(video_id: int, edited_path: str):
    """편집된 영상 경로 업데이트"""
    conn = get_connection()
    conn.execute(
        "UPDATE videos SET edited_path = ?, status = 'edited' WHERE id = ?",
        (edited_path, video_id)
    )
    conn.commit()
    conn.close()


def insert_post(product_id: int, video_id: int, post_id: str,
                caption: str, hashtags: str) -> int:
    """게시물 정보 저장 후 ID 반환"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO posts (product_id, video_id, post_id, caption, hashtags, upload_time)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (product_id, video_id, post_id, caption, hashtags, datetime.now().isoformat())
    )
    conn.commit()
    post_id_db = cursor.lastrowid
    conn.close()
    return post_id_db


def insert_interaction(post_id: int, comment_id: str,
                       commenter_username: str, comment_text: str) -> int:
    """댓글 상호작용 기록 저장"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO interactions (post_id, comment_id, commenter_username, comment_text)
           VALUES (?, ?, ?, ?)""",
        (post_id, comment_id, commenter_username, comment_text)
    )
    conn.commit()
    interaction_id = cursor.lastrowid
    conn.close()
    return interaction_id


def mark_interaction_replied(interaction_id: int, dm_sent: bool = False):
    """상호작용 처리 완료 표시"""
    conn = get_connection()
    conn.execute(
        "UPDATE interactions SET reply_sent = 1, dm_sent = ? WHERE id = ?",
        (1 if dm_sent else 0, interaction_id)
    )
    conn.commit()
    conn.close()


def is_comment_processed(comment_id: str) -> bool:
    """이미 처리된 댓글인지 확인"""
    if not comment_id:
        return False
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM interactions WHERE comment_id = ? LIMIT 1",
        (comment_id,)
    ).fetchone()
    conn.close()
    return row is not None


def start_run_log(run_type: str = "auto") -> int:
    """실행 로그 시작"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO run_logs (run_type) VALUES (?)", (run_type,))
    conn.commit()
    run_id = cursor.lastrowid
    conn.close()
    return run_id


def finish_run_log(run_id: int, products: int = 0, videos: int = 0,
                   posts: int = 0, dms: int = 0, status: str = "completed",
                   error: str = ""):
    """실행 로그 완료 처리"""
    conn = get_connection()
    conn.execute(
        """UPDATE run_logs SET finished_at = ?, products_processed = ?,
           videos_created = ?, posts_uploaded = ?, dms_sent = ?,
           status = ?, error_message = ? WHERE id = ?""",
        (datetime.now().isoformat(), products, videos, posts, dms, status, error, run_id)
    )
    conn.commit()
    conn.close()


def get_recent_logs(limit: int = 10) -> list:
    """최근 실행 로그 조회"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM run_logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """전체 통계 조회"""
    conn = get_connection()
    stats = {
        "total_products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "total_videos": conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
        "total_posts": conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "total_interactions": conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0],
        "total_dms": conn.execute(
            "SELECT COUNT(*) FROM interactions WHERE dm_sent = 1"
        ).fetchone()[0],
    }
    conn.close()
    return stats


def is_url_processed(url: str) -> bool:
    """이미 처리된 URL인지 확인 (중복 방지)"""
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM videos WHERE original_url = ?", (url,)
    ).fetchone()[0]
    conn.close()
    return count > 0
