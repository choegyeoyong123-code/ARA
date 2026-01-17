import sqlite3
import json

def init_db():
    conn = sqlite3.connect("history.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS history (user_id TEXT PRIMARY KEY, messages TEXT)")
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
    cursor.execute("INSERT OR REPLACE INTO history (user_id, messages) VALUES (?, ?)", (user_id, json.dumps(messages)))
    conn.commit()
    conn.close()