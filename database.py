import sqlite3
import json

def init_db():
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (user_id TEXT PRIMARY KEY, messages TEXT)''')
    conn.commit()
    conn.close()

def get_history(user_id):
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("SELECT messages FROM history WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return json.loads(row[0]) if row else []

def save_history(user_id, messages):
    # 최근 10개의 메시지만 저장 (성능 최적화)
    data = json.dumps(messages[-10:])
    conn = sqlite3.connect('memory.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO history VALUES (?, ?)", (user_id, data))
    conn.commit()
    conn.close()