import sqlite3
import json
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

# 멀티턴 컨텍스트(pending_state) TTL: 오래된 상태가 버튼 입력에 간섭하지 않도록 자동 만료
_PENDING_STATE_TTL_SECONDS = int((__import__("os").environ.get("PENDING_STATE_TTL_SECONDS") or "180").strip())

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

    # 사용자 인터랙션 로그(FAQ 자동 업데이트 프로토타입용)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS interaction_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            intent TEXT,
            user_query TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    # FAQ 자동 업데이트(프로토타입): interaction_log 집계 결과를 저장(응답에 직접 사용하지 않음)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS faq_autogen (
            intent TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0,
            example_queries TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    # 사용자 제보(맛집 등) 저장
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_contributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            text TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS restaurant_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            reported_text TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    # 사용자 제보(요구사항 명시: user_contribution_db) — 구조화 저장(프로토타입)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_contribution_db (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            place_name TEXT,
            address TEXT,
            note TEXT,
            raw_text TEXT,
            is_yeongdo INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            reviewed_by TEXT,
            review_note TEXT,
            reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    # 마이그레이션: user_contribution_db 컬럼 누락 시 추가
    try:
        cursor.execute("PRAGMA table_info(user_contribution_db)")
        cols_uc = {row[1] for row in cursor.fetchall()}
        if "status" not in cols_uc:
            cursor.execute("ALTER TABLE user_contribution_db ADD COLUMN status TEXT DEFAULT 'pending'")
        if "reviewed_by" not in cols_uc:
            cursor.execute("ALTER TABLE user_contribution_db ADD COLUMN reviewed_by TEXT")
        if "review_note" not in cols_uc:
            cursor.execute("ALTER TABLE user_contribution_db ADD COLUMN review_note TEXT")
        if "reviewed_at" not in cols_uc:
            cursor.execute("ALTER TABLE user_contribution_db ADD COLUMN reviewed_at TEXT")
    except Exception:
        pass

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

def log_interaction(user_id: Optional[str], intent: str, user_query: str) -> None:
    """
    사용자의 질문 패턴/빈도 집계를 위한 로그(프로토타입).
    - 응답 생성에 직접 사용하지 않음(환각 방지)
    """
    if not user_id:
        return
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO interaction_log (user_id, intent, user_query) VALUES (?, ?, ?)",
            (user_id, (intent or "").strip(), (user_query or "").strip()),
        )
        conn.commit()
        conn.close()
    except Exception:
        return

def save_food_contribution(user_id: Optional[str], text: str) -> None:
    """
    맛집 제보 저장(프로토타입).
    - 정규화/검증은 추후(현재는 raw 텍스트 저장)
    """
    if not user_id:
        return
    t = (text or "").strip()
    if not t:
        return
    # 간단 파싱: "가게명 / 주소(영도구) / 한 줄 추천"
    place_name: str | None = None
    address: str | None = None
    note: str | None = None
    try:
        parts = [p.strip() for p in re_split_slash_like(t) if p.strip()]
        if parts:
            place_name = parts[0][:100]
        if len(parts) >= 2:
            address = parts[1][:200]
        if len(parts) >= 3:
            note = " / ".join(parts[2:])[:200]
    except Exception:
        place_name, address, note = None, None, None

    is_yeongdo = 0
    try:
        blob = (address or t)
        if ("영도구" in blob) or ("Yeongdo" in blob) or ("yeongdo" in blob):
            is_yeongdo = 1
    except Exception:
        is_yeongdo = 0
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        # 신규(구조화) 저장: user_contribution_db
        cursor.execute(
            """
            INSERT INTO user_contribution_db (user_id, place_name, address, note, raw_text, is_yeongdo, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (user_id, place_name, address, note, t, int(is_yeongdo)),
        )
        # 레거시 테이블도 유지(호환)
        cursor.execute(
            "INSERT INTO user_contributions (user_id, text) VALUES (?, ?)",
            (user_id, t),
        )
        conn.commit()
        conn.close()
    except Exception:
        return

def save_restaurant_report(user_id: Optional[str], reported_text: str) -> None:
    if not user_id:
        return
    t = (reported_text or "").strip()
    if not t:
        return
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO restaurant_reports (user_id, reported_text) VALUES (?, ?)",
            (user_id, t),
        )
        conn.commit()
        conn.close()
    except Exception:
        return

def re_split_slash_like(text: str) -> List[str]:
    """
    '가게명 / 주소 / 추천' 류 입력을 슬래시/구분자로 분리합니다.
    - DB 레이어에서만 사용(프로토타입)
    """
    s = (text or "").strip()
    if not s:
        return []
    # '/', '／', '|', ',' 등을 구분자로 취급
    import re
    return re.split(r"\s*(?:/|／|\||,|;)\s*", s)

def list_user_contribution_db(status: Optional[str] = "pending", limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """
    user_contribution_db 목록 조회(관리자용).
    status:
      - pending / approved / rejected / None(전체)
    """
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        lim = max(1, min(int(limit or 50), 200))
        off = max(0, int(offset or 0))
        if status:
            cursor.execute(
                """
                SELECT id, user_id, place_name, address, note, raw_text, is_yeongdo, status, reviewed_by, review_note, reviewed_at, created_at
                FROM user_contribution_db
                WHERE status = ?
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (str(status), lim, off),
            )
        else:
            cursor.execute(
                """
                SELECT id, user_id, place_name, address, note, raw_text, is_yeongdo, status, reviewed_by, review_note, reviewed_at, created_at
                FROM user_contribution_db
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (lim, off),
            )
        rows = cursor.fetchall()
        conn.close()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r[0]),
                    "user_id": r[1],
                    "place_name": r[2],
                    "address": r[3],
                    "note": r[4],
                    "raw_text": r[5],
                    "is_yeongdo": int(r[6] or 0),
                    "status": r[7] or "pending",
                    "reviewed_by": r[8],
                    "review_note": r[9],
                    "reviewed_at": r[10],
                    "created_at": r[11],
                }
            )
        return out
    except Exception:
        return []

def set_user_contribution_status(
    contribution_id: int,
    status: str,
    reviewed_by: Optional[str] = None,
    review_note: Optional[str] = None,
) -> bool:
    """
    제보 상태 변경(관리자용).
    status: pending / approved / rejected
    """
    st = (status or "").strip().lower()
    if st not in {"pending", "approved", "rejected"}:
        return False
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE user_contribution_db
            SET status = ?, reviewed_by = ?, review_note = ?, reviewed_at = datetime('now')
            WHERE id = ?
            """,
            (st, (reviewed_by or None), (review_note or None), int(contribution_id)),
        )
        conn.commit()
        changed = (cursor.rowcount or 0) > 0
        conn.close()
        return bool(changed)
    except Exception:
        return False

def search_approved_contributions(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    승인된(approved) 제보에서 검색(챗봇 폴백용).
    - 환각 방지: DB에 저장된 텍스트만 반환
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        lim = max(1, min(int(limit or 5), 10))
        like = f"%{q}%"
        cursor.execute(
            """
            SELECT id, place_name, address, note, raw_text, is_yeongdo, created_at
            FROM user_contribution_db
            WHERE status = 'approved'
              AND (
                LOWER(COALESCE(place_name, '')) LIKE ?
                OR LOWER(COALESCE(address, '')) LIKE ?
                OR LOWER(COALESCE(note, '')) LIKE ?
                OR LOWER(COALESCE(raw_text, '')) LIKE ?
              )
            ORDER BY id DESC
            LIMIT ?
            """,
            (like, like, like, like, lim),
        )
        rows = cursor.fetchall()
        conn.close()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r[0]),
                    "name": (r[1] or "").strip(),
                    "addr": (r[2] or "").strip(),
                    "recommendation": (r[3] or "").strip() or (r[4] or "").strip(),
                    "is_yeongdo": int(r[5] or 0),
                    "created_at": r[6],
                    "source": "user_contribution_db",
                }
            )
        return out
    except Exception:
        return []

def get_top_intents(limit: int = 10) -> List[Dict[str, Any]]:
    """
    최근 누적 기준 intent Top-N (FAQ 업데이트 프로토타입 참고용)
    """
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT intent, COUNT(*) as cnt
            FROM interaction_log
            GROUP BY intent
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"intent": r[0] or "", "count": int(r[1] or 0)} for r in rows]
    except Exception:
        return []

def update_faq_autogen(limit_intents: int = 10, examples_per_intent: int = 5, min_count: int = 3) -> List[Dict[str, Any]]:
    """
    interaction_log를 기반으로 자주 묻는 질문(FAQ) 후보를 갱신하는 프로토타입.
    - 환각 방지: '답변'을 생성하지 않고, intent/예시질문만 저장합니다.
    - 반환: 업데이트된 intent 목록
    """
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT intent, COUNT(*) as cnt
            FROM interaction_log
            WHERE intent IS NOT NULL AND TRIM(intent) != ''
            GROUP BY intent
            HAVING COUNT(*) >= ?
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (int(min_count), int(limit_intents)),
        )
        intents = cursor.fetchall()
        updated: List[Dict[str, Any]] = []
        for intent, cnt in intents:
            cursor.execute(
                """
                SELECT user_query
                FROM interaction_log
                WHERE intent = ? AND user_query IS NOT NULL AND TRIM(user_query) != ''
                ORDER BY id DESC
                LIMIT ?
                """,
                (intent, int(examples_per_intent)),
            )
            ex_rows = cursor.fetchall()
            examples = [str(r[0]) for r in ex_rows if r and r[0]]
            cursor.execute(
                """
                INSERT OR REPLACE INTO faq_autogen (intent, count, example_queries, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (str(intent), int(cnt or 0), json.dumps(examples, ensure_ascii=False)),
            )
            updated.append({"intent": str(intent), "count": int(cnt or 0), "examples": examples})
        conn.commit()
        conn.close()
        return updated
    except Exception:
        return []

def get_faq_autogen(limit: int = 10) -> List[Dict[str, Any]]:
    """
    저장된 FAQ 후보(프로토타입) 조회.
    """
    try:
        conn = sqlite3.connect("history.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT intent, count, example_queries, updated_at FROM faq_autogen ORDER BY count DESC LIMIT ?",
            (int(limit),),
        )
        rows = cursor.fetchall()
        conn.close()
        out: List[Dict[str, Any]] = []
        for intent, cnt, ex_json, updated_at in rows:
            try:
                examples = json.loads(ex_json) if ex_json else []
                if not isinstance(examples, list):
                    examples = []
            except Exception:
                examples = []
            out.append(
                {
                    "intent": str(intent or ""),
                    "count": int(cnt or 0),
                    "examples": examples,
                    "updated_at": updated_at,
                }
            )
        return out
    except Exception:
        return []

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
    k = (kind or "").strip()
    if not k:
        return None

    # TTL 만료 처리(버튼 중복/이전 컨텍스트 간섭 방지)
    if updated_at:
        try:
            # sqlite datetime('now') 형식: "YYYY-MM-DD HH:MM:SS"
            ts = datetime.strptime(str(updated_at), "%Y-%m-%d %H:%M:%S")
            if _PENDING_STATE_TTL_SECONDS > 0 and (datetime.utcnow() - ts) > timedelta(seconds=_PENDING_STATE_TTL_SECONDS):
                try:
                    clear_pending_state(user_id)
                except Exception:
                    pass
                return None
        except Exception:
            # 파싱 실패 시: 보수적으로 유지(즉시 삭제하지 않음)
            pass

    return k

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