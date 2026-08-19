import sqlite3
import hashlib
import secrets
import time
from typing import Optional
from pathlib import Path

DB_PATH = Path("kataru.db")


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            full_name TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            provider TEXT DEFAULT 'local',
            provider_id TEXT DEFAULT '',
            language TEXT DEFAULT 'english',
            voice_speed REAL DEFAULT 1.0,
            notifications_enabled INTEGER DEFAULT 1,
            sound_enabled INTEGER DEFAULT 1,
            theme TEXT DEFAULT 'dark',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            message_role TEXT NOT NULL,
            message_text TEXT NOT NULL,
            sentiment TEXT DEFAULT 'neutral',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            context_key TEXT NOT NULL,
            context_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, context_key)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            ticket_id TEXT UNIQUE NOT NULL,
            issue_type TEXT DEFAULT 'general',
            summary TEXT DEFAULT '',
            collected_info TEXT DEFAULT '{}',
            conversation_log TEXT DEFAULT '[]',
            status TEXT DEFAULT 'open',
            priority TEXT DEFAULT 'normal',
            language TEXT DEFAULT 'english',
            escalated INTEGER DEFAULT 0,
            escalation_reason TEXT DEFAULT '',
            resolution TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_history(user_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_context_user ON user_context(user_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)
    """)

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def verify_password(password: str, stored_hash: str) -> bool:
    if ":" not in stored_hash:
        return False
    salt, hashed = stored_hash.split(":", 1)
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == hashed


def create_user(username: str, email: str, password: str, full_name: str = "", provider: str = "local") -> dict:
    conn = get_db()
    cursor = conn.cursor()

    try:
        password_hash = hash_password(password) if password else ""
        cursor.execute(
            """INSERT INTO users (username, email, password_hash, full_name, provider)
               VALUES (?, ?, ?, ?, ?)""",
            (username, email, password_hash, full_name, provider),
        )
        conn.commit()
        user_id = cursor.lastrowid

        user = dict(cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        conn.close()
        return {"success": True, "user": user}
    except sqlite3.IntegrityError as e:
        conn.close()
        if "username" in str(e):
            return {"success": False, "error": "Username already exists"}
        elif "email" in str(e):
            return {"success": False, "error": "Email already exists"}
        return {"success": False, "error": "User already exists"}


def authenticate_user(identifier: str, password: str) -> dict:
    conn = get_db()
    cursor = conn.cursor()

    user = cursor.execute(
        "SELECT * FROM users WHERE username = ? OR email = ?",
        (identifier, identifier),
    ).fetchone()

    if not user:
        conn.close()
        return {"success": False, "error": "User not found"}

    user_dict = dict(user)

    if user_dict.get("password_hash"):
        if not verify_password(password, user_dict["password_hash"]):
            conn.close()
            return {"success": False, "error": "Invalid password"}

    cursor.execute(
        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
        (user_id := user_dict["id"],),
    )
    conn.commit()
    conn.close()

    return {"success": True, "user": user_dict}


def authenticate_oauth(username: str, email: str, full_name: str, provider: str, provider_id: str) -> dict:
    conn = get_db()
    cursor = conn.cursor()

    user = cursor.execute(
        "SELECT * FROM users WHERE provider = ? AND provider_id = ?",
        (provider, provider_id),
    ).fetchone()

    if user:
        user_dict = dict(user)
        cursor.execute(
            "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
            (user_dict["id"],),
        )
        conn.commit()
        conn.close()
        return {"success": True, "user": user_dict, "is_new": False}

    try:
        password_hash = secrets.token_hex(32)
        cursor.execute(
            """INSERT INTO users (username, email, password_hash, full_name, provider, provider_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (username, email, password_hash, full_name, provider, provider_id),
        )
        conn.commit()
        user_id = cursor.lastrowid
        user = dict(cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        conn.close()
        return {"success": True, "user": user, "is_new": True}
    except sqlite3.IntegrityError:
        conn.close()
        return {"success": False, "error": "Account already exists"}


def get_user(user_id: int) -> Optional[dict]:
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def update_user(user_id: int, **kwargs) -> bool:
    allowed_fields = [
        "full_name", "avatar_url", "language", "voice_speed",
        "notifications_enabled", "sound_enabled", "theme"
    ]
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not updates:
        return False

    conn = get_db()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user_id]
    conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def save_chat_message(user_id: int, session_id: str, role: str, text: str, sentiment: str = "neutral"):
    conn = get_db()
    conn.execute(
        """INSERT INTO chat_history (user_id, session_id, message_role, message_text, sentiment)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, session_id, role, text, sentiment),
    )
    conn.commit()
    conn.close()


def get_chat_history(user_id: int, session_id: str = None, limit: int = 50) -> list:
    conn = get_db()
    if session_id:
        rows = conn.execute(
            """SELECT * FROM chat_history
               WHERE user_id = ? AND session_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, session_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT session_id, MIN(created_at) as started_at,
                      COUNT(*) as message_count,
                      MIN(message_text) as preview
               FROM chat_history
               WHERE user_id = ?
               GROUP BY session_id
               ORDER BY started_at DESC LIMIT 20""",
            (user_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_user_context(user_id: int, key: str, value: str):
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO user_context (user_id, context_key, context_value, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
        (user_id, key, value),
    )
    conn.commit()
    conn.close()


def get_user_context(user_id: int) -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT context_key, context_value FROM user_context WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    conn.close()
    return {r["context_key"]: r["context_value"] for r in rows}


def delete_chat_session(user_id: int, session_id: str) -> bool:
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM chat_history WHERE user_id = ? AND session_id = ?",
        (user_id, session_id),
    )
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def get_user_stats(user_id: int) -> dict:
    conn = get_db()

    total_chats = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM chat_history WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]

    total_messages = conn.execute(
        "SELECT COUNT(*) FROM chat_history WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]

    active_days = conn.execute(
        """SELECT COUNT(DISTINCT DATE(created_at))
           FROM chat_history WHERE user_id = ?""",
        (user_id,),
    ).fetchone()[0]

    total_tickets = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]

    open_tickets = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE user_id = ? AND status IN ('open', 'escalated')",
        (user_id,),
    ).fetchone()[0]

    conn.close()

    return {
        "total_chats": total_chats,
        "total_messages": total_messages,
        "active_days": active_days,
        "total_tickets": total_tickets,
        "open_tickets": open_tickets,
    }


def create_ticket(user_id: int, session_id: str, issue_type: str, summary: str,
                  collected_info: dict = None, conversation_log: list = None,
                  language: str = "english", priority: str = "normal") -> dict:
    import json
    import uuid
    conn = get_db()
    ticket_id = f"KTR-{uuid.uuid4().hex[:8].upper()}"
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO tickets (user_id, session_id, ticket_id, issue_type, summary,
           collected_info, conversation_log, status, priority, language)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        (user_id, session_id, ticket_id, issue_type, summary,
         json.dumps(collected_info or {}), json.dumps(conversation_log or []),
         priority, language),
    )
    conn.commit()
    ticket = dict(cursor.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone())
    conn.close()
    return {"success": True, "ticket": ticket}


def escalate_ticket(ticket_id: str, reason: str) -> bool:
    import json
    conn = get_db()
    conn.execute(
        """UPDATE tickets SET status = 'escalated', escalated = 1,
           escalation_reason = ?, updated_at = CURRENT_TIMESTAMP
           WHERE ticket_id = ?""",
        (reason, ticket_id),
    )
    conn.commit()
    conn.close()
    return True


def resolve_ticket(ticket_id: str, resolution: str) -> bool:
    conn = get_db()
    conn.execute(
        """UPDATE tickets SET status = 'resolved', resolution = ?,
           updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?""",
        (resolution, ticket_id),
    )
    conn.commit()
    conn.close()
    return True


def get_user_tickets(user_id: int, status: str = None) -> list:
    conn = get_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
            (user_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ticket(ticket_id: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


init_db()
