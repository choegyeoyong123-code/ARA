import sqlite3
import json
from typing import Any, Dict, List, Optional

def init_db():
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS history (user_id TEXT PRIMARY KEY, messages TEXT)")

    # 사용자 세션 설정(멀티언어 등) — 멀티 워커 안전
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT PRIMARY KEY,
            session_lang TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    # 멀티턴 상태 저장(멀티 워커 안전)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_state (
            user_id TEXT PRIMARY KEY,
            kind TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    # 대화/피드백 저장 테이블 (Self-Improvement Loop)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            user_id TEXT,
            user_query TEXT,
            ai_answer TEXT,
            tools_used TEXT,
            user_feedback INTEGER DEFAULT 0,
            is_gold_standard INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    # 기존 테이블 마이그레이션: 컬럼 누락 시 추가
    cursor.execute("PRAGMA table_info(conversations)")
    cols = {row[1] for row in cursor.fetchall()}  # row[1] == name

    if "user_feedback" not in cols:
        cursor.execute("ALTER TABLE conversations ADD COLUMN user_feedback INTEGER DEFAULT 0")
    if "is_gold_standard" not in cols:
        cursor.execute("ALTER TABLE conversations ADD COLUMN is_gold_standard INTEGER DEFAULT 0")
    if "tools_used" not in cols:
        cursor.execute("ALTER TABLE conversations ADD COLUMN tools_used TEXT")
    if "user_id" not in cols:
        cursor.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT")
    if "user_query" not in cols:
        cursor.execute("ALTER TABLE conversations ADD COLUMN user_query TEXT")
    if "ai_answer" not in cols:
        cursor.execute("ALTER TABLE conversations ADD COLUMN ai_answer TEXT")
    if "created_at" not in cols:
        cursor.execute("ALTER TABLE conversations ADD COLUMN created_at TEXT")

    conn.commit()
    conn.close()

def get_user_lang(user_id: str) -> Optional[str]:
    if not user_id:
        return None
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute("SELECT session_lang FROM user_settings WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    lang = (row[0] or "").strip().lower()
    return lang if lang in {"ko", "en"} else None

def set_user_lang(user_id: str, session_lang: str) -> None:
    if not user_id:
        return
    lang = (session_lang or "").strip().lower()
    if lang not in {"ko", "en"}:
        return
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO user_settings (user_id, session_lang, updated_at) VALUES (?, ?, datetime('now'))",
        (user_id, lang),
    )
    conn.commit()
    conn.close()

def get_pending_state(user_id: str) -> Optional[str]:
    if not user_id:
        return None
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute("SELECT kind, updated_at FROM pending_state WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    kind, updated_at = row[0], row[1]
    return kind or None

def set_pending_state(user_id: str, kind: str) -> None:
    if not user_id:
        return
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO pending_state (user_id, kind, updated_at) VALUES (?, ?, datetime('now'))",
        (user_id, kind),
    )
    conn.commit()
    conn.close()

def clear_pending_state(user_id: str) -> None:
    if not user_id:
        return
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pending_state WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_history(user_id):
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute("SELECT messages FROM history WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return json.loads(row[0]) if row else []

def save_history(user_id, messages):
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    # 리스트 내 객체들이 dict 형태인지 확인 후 저장
    cursor.execute("INSERT OR REPLACE INTO history (user_id, messages) VALUES (?, ?)", 
                   (user_id, json.dumps(messages)))
    conn.commit()
    conn.close()

def save_conversation_pair(
    conversation_id: str,
    user_id: Optional[str],
    user_query: str,
    ai_answer: str,
    tools_used: Optional[List[Dict[str, Any]]] = None,
    user_feedback: int = 0,
    is_gold_standard: bool = False,
) -> None:
    """
    질문/답변/사용 도구/피드백을 한 쌍으로 저장합니다.
    tools_used는 [{"name": "...", "arguments": {...}}] 형태를 권장합니다.
    """
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO conversations
        (conversation_id, user_id, user_query, ai_answer, tools_used, user_feedback, is_gold_standard)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            user_id,
            user_query,
            ai_answer,
            json.dumps(tools_used or [], ensure_ascii=False),
            int(user_feedback),
            1 if is_gold_standard else 0,
        ),
    )
    conn.commit()
    conn.close()

def update_conversation_feedback(
    conversation_id: str,
    user_feedback: int,
    is_gold_standard: Optional[bool] = None,
) -> bool:
    """
    특정 conversation_id에 피드백을 기록합니다.
    - user_feedback: 1(긍정), -1(부정), 0(중립)
    - is_gold_standard는 주어졌을 때만 갱신합니다.
    """
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    if is_gold_standard is None:
        cursor.execute(
            "UPDATE conversations SET user_feedback = ? WHERE conversation_id = ?",
            (int(user_feedback), conversation_id),
        )
    else:
        cursor.execute(
            "UPDATE conversations SET user_feedback = ?, is_gold_standard = ? WHERE conversation_id = ?",
            (int(user_feedback), 1 if is_gold_standard else 0, conversation_id),
        )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed

def get_success_examples(limit: int = 5) -> List[Dict[str, Any]]:
    """
    is_gold_standard=1 또는 user_feedback>0 인 성공 사례를 무작위로 추출합니다.
    (동점 처리: gold 우선 + 무작위)
    """
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT user_query, ai_answer
        FROM conversations
        WHERE is_gold_standard = 1 OR user_feedback > 0
        ORDER BY is_gold_standard DESC, user_feedback DESC, RANDOM()
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"user_query": r[0] or "", "ai_answer": r[1] or ""} for r in rows]