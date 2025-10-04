# database.py
"""
Robust VoiceTrackerDatabase:
- Tries to use system sqlite3
- If sqlite3 import fails, tries pysqlite3
- If still unavailable, falls back to a JSON file store (persisted)
"""

import os
import json
import threading
from datetime import datetime

# Attempt sqlite imports with fallback
SQLITE_AVAILABLE = False
sqlite3 = None
try:
    import sqlite3 as _sqlite3
    sqlite3 = _sqlite3
    SQLITE_AVAILABLE = True
except Exception:
    try:
        # pysqlite3 provides a binary wheel on some hosts
        from pysqlite3 import dbapi2 as _pysqlite3
        sqlite3 = _pysqlite3
        SQLITE_AVAILABLE = True
    except Exception:
        SQLITE_AVAILABLE = False
        sqlite3 = None

class VoiceTrackerDatabase:
    def __init__(self, db_path: str = "voice_tracker.db"):
        self.db_path = db_path
        self.lock = threading.Lock()
        if SQLITE_AVAILABLE:
            # ensure directory exists
            dirpath = os.path.dirname(self.db_path)
            if dirpath:
                os.makedirs(dirpath, exist_ok=True)
            self.memory_db = None
            self._init_sqlite()
            print("✅ Database initialized (SQLite)")
        else:
            # JSON fallback
            self.json_path = self.db_path + ".json"
            dirpath = os.path.dirname(self.json_path)
            if dirpath:
                os.makedirs(dirpath, exist_ok=True)
            self._init_json_store()
            print("⚠️ sqlite3 not available — using JSON fallback store")

    # ---------------- SQLite helpers ----------------
    def _init_sqlite(self):
        conn = self.get_connection()
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS streamers (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                total_stream_time REAL DEFAULT 0,
                stream_sessions INTEGER DEFAULT 0,
                last_streamed TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS voice_time (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                total_voice_time REAL DEFAULT 0,
                voice_sessions INTEGER DEFAULT 0,
                last_joined TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS active_sessions (
                user_id INTEGER PRIMARY KEY,
                session_type TEXT,
                start_time TEXT,
                channel_id INTEGER
            )
        ''')

        conn.commit()
        conn.close()

    def get_connection(self):
        try:
            return sqlite3.connect(self.db_path, check_same_thread=False)
        except Exception:
            # fallback to in-memory sqlite if file connection fails
            if getattr(self, "memory_db", None) is None:
                self.memory_db = sqlite3.connect(':memory:', check_same_thread=False)
                self._init_memory_tables(self.memory_db)
            return self.memory_db

    def _init_memory_tables(self, conn):
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS streamers (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                total_stream_time REAL DEFAULT 0,
                stream_sessions INTEGER DEFAULT 0,
                last_streamed TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS voice_time (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                total_voice_time REAL DEFAULT 0,
                voice_sessions INTEGER DEFAULT 0,
                last_joined TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS active_sessions (
                user_id INTEGER PRIMARY KEY,
                session_type TEXT,
                start_time TEXT,
                channel_id INTEGER
            )
        ''')
        conn.commit()

    # ---------------- JSON fallback ----------------
    def _init_json_store(self):
        if not os.path.exists(self.json_path):
            data = {"streamers": {}, "voice_time": {}, "active_sessions": {}}
            self._write_json(data)

    def _read_json(self):
        with self.lock:
            with open(self.json_path, "r", encoding="utf-8") as f:
                return json.load(f)

    def _write_json(self, data):
        with self.lock:
            tmp = self.json_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.json_path)

    # ---------------- utils ----------------
    def _now_iso(self):
        return datetime.now().replace(microsecond=0).isoformat(sep=' ')

    def _parse_time(self, s):
        # try a couple of common formats
        try:
            return datetime.fromisoformat(s)
        except Exception:
            from datetime import datetime as dt
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return dt.strptime(s, fmt)
                except Exception:
                    pass
        # as a last resort, raise
        raise ValueError(f"Unrecognized time format: {s}")

    # ---------------- public API ----------------
    def start_voice_session(self, user_id, username, channel_id):
        if SQLITE_AVAILABLE:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO active_sessions
                (user_id, session_type, start_time, channel_id)
                VALUES (?, 'voice', datetime('now'), ?)
            ''', (user_id, channel_id))
            conn.commit()
            conn.close()
        else:
            data = self._read_json()
            data["active_sessions"][str(user_id)] = {
                "session_type": "voice",
                "start_time": self._now_iso(),
                "channel_id": channel_id
            }
            self._write_json(data)

        print(f"🎧 Voice session started for {username}")

    def end_voice_session(self, user_id):
        if SQLITE_AVAILABLE:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute('SELECT start_time FROM active_sessions WHERE user_id = ? AND session_type = ?', (user_id, 'voice'))
            row = c.fetchone()
            if not row:
                conn.close()
                return 0
            start_time_str = row[0]
            try:
                start_time = self._parse_time(start_time_str)
            except Exception:
                start_time = datetime.now()  # fallback to zero duration
            duration = (datetime.now() - start_time).total_seconds()

            # read current
            c.execute('SELECT total_voice_time FROM voice_time WHERE user_id = ?', (user_id,))
            r = c.fetchone()
            if r:
                new_total = (r[0] or 0) + duration
                c.execute('UPDATE voice_time SET total_voice_time = ?, voice_sessions = voice_sessions + 1, last_joined = datetime("now") WHERE user_id = ?', (new_total, user_id))
            else:
                c.execute('INSERT INTO voice_time (user_id, username, total_voice_time, voice_sessions, last_joined) VALUES (?, ?, ?, ?, datetime("now"))', (user_id, f"User_{user_id}", duration, 1))

            c.execute('DELETE FROM active_sessions WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            print(f"⏱️ Recorded {duration/60:.1f} minutes voice time")
            return duration / 60
        else:
            data = self._read_json()
            sess = data["active_sessions"].get(str(user_id))
            if not sess or sess.get("session_type") != "voice":
                return 0
            start_time = self._parse_time(sess["start_time"])
            duration = (datetime.now() - start_time).total_seconds()

            vt = data["voice_time"].get(str(user_id), {"user_id": user_id, "username": f"User_{user_id}", "total_voice_time": 0, "voice_sessions": 0})
            vt["total_voice_time"] = vt.get("total_voice_time", 0) + duration
            vt["voice_sessions"] = vt.get("voice_sessions", 0) + 1
            vt["last_joined"] = self._now_iso()
            data["voice_time"][str(user_id)] = vt

            del data["active_sessions"][str(user_id)]
            self._write_json(data)
            print(f"⏱️ Recorded {duration/60:.1f} minutes voice time (JSON fallback)")
            return duration / 60

    def start_stream_session(self, user_id, username, channel_id):
        if SQLITE_AVAILABLE:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO active_sessions
                (user_id, session_type, start_time, channel_id)
                VALUES (?, 'stream', datetime('now'), ?)
            ''', (user_id, channel_id))
            conn.commit()
            conn.close()
        else:
            data = self._read_json()
            data["active_sessions"][str(user_id)] = {
                "session_type": "stream",
                "start_time": self._now_iso(),
                "channel_id": channel_id
            }
            self._write_json(data)
        print(f"🎬 Stream session started for {username}")

    def end_stream_session(self, user_id):
        if SQLITE_AVAILABLE:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute('SELECT start_time FROM active_sessions WHERE user_id = ? AND session_type = ?', (user_id, 'stream'))
            row = c.fetchone()
            if not row:
                conn.close()
                return 0
            start_time_str = row[0]
            try:
                start_time = self._parse_time(start_time_str)
            except Exception:
                start_time = datetime.now()
            duration = (datetime.now() - start_time).total_seconds()

            c.execute('SELECT total_stream_time FROM streamers WHERE user_id = ?', (user_id,))
            r = c.fetchone()
            if r:
                new_total = (r[0] or 0) + duration
                c.execute('UPDATE streamers SET total_stream_time = ?, stream_sessions = stream_sessions + 1, last_streamed = datetime("now") WHERE user_id = ?', (new_total, user_id))
            else:
                c.execute('INSERT INTO streamers (user_id, username, total_stream_time, stream_sessions, last_streamed) VALUES (?, ?, ?, ?, datetime("now"))', (user_id, f"User_{user_id}", duration, 1))

            c.execute('DELETE FROM active_sessions WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            print(f"⏱️ Recorded {duration/60:.1f} minutes stream time")
            return duration / 60
        else:
            data = self._read_json()
            sess = data["active_sessions"].get(str(user_id))
            if not sess or sess.get("session_type") != "stream":
                return 0
            start_time = self._parse_time(sess["start_time"])
            duration = (datetime.now() - start_time).total_seconds()

            st = data["streamers"].get(str(user_id), {"user_id": user_id, "username": f"User_{user_id}", "total_stream_time": 0, "stream_sessions": 0})
            st["total_stream_time"] = st.get("total_stream_time", 0) + duration
            st["stream_sessions"] = st.get("stream_sessions", 0) + 1
            st["last_streamed"] = self._now_iso()
            data["streamers"][str(user_id)] = st

            del data["active_sessions"][str(user_id)]
            self._write_json(data)
            print(f"⏱️ Recorded {duration/60:.1f} minutes stream time (JSON fallback)")
            return duration / 60

    def get_top_voice_users(self, limit=5):
        if SQLITE_AVAILABLE:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute('SELECT user_id, username, total_voice_time, voice_sessions FROM voice_time ORDER BY total_voice_time DESC LIMIT ?', (limit,))
            rows = c.fetchall()
            conn.close()
            return [{'user_id': row[0], 'username': row[1], 'total_voice_time': row[2], 'sessions': row[3]} for row in rows]
        else:
            data = self._read_json()
            items = []
            for k, v in data["voice_time"].items():
                items.append({
                    "user_id": int(k),
                    "username": v.get("username"),
                    "total_voice_time": v.get("total_voice_time", 0),
                    "sessions": v.get("voice_sessions", 0)
                })
            items.sort(key=lambda x: x["total_voice_time"], reverse=True)
            return items[:limit]

    def get_top_streamers(self, limit=5):
        if SQLITE_AVAILABLE:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute('SELECT user_id, username, total_stream_time, stream_sessions FROM streamers ORDER BY total_stream_time DESC LIMIT ?', (limit,))
            rows = c.fetchall()
            conn.close()
            return [{'user_id': row[0], 'username': row[1], 'total_stream_time': row[2], 'sessions': row[3]} for row in rows]
        else:
            data = self._read_json()
            items = []
            for k, v in data["streamers"].items():
                items.append({
                    "user_id": int(k),
                    "username": v.get("username"),
                    "total_stream_time": v.get("total_stream_time", 0),
                    "sessions": v.get("stream_sessions", 0)
                })
            items.sort(key=lambda x: x["total_stream_time"], reverse=True)
            return items[:limit]

    def get_user_watch_stats(self, user_id):
        if SQLITE_AVAILABLE:
            conn = self.get_connection()
            c = conn.cursor()
            c.execute('SELECT total_voice_time, voice_sessions FROM voice_time WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            conn.close()
            if row:
                return {'total_voice_time': row[0], 'sessions': row[1]}
            return None
        else:
            data = self._read_json()
            v = data["voice_time"].get(str(user_id))
            if v:
                return {'total_voice_time': v.get("total_voice_time", 0), 'sessions': v.get("voice_sessions", 0)}
            return None
