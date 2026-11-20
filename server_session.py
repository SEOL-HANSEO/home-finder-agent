"""Opaque browser cookies backed by a local server-side session store."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import re
import secrets
import sqlite3

from flask.sessions import SessionInterface, SessionMixin


class ServerSession(dict, SessionMixin):
    def __init__(self, data=None, sid=None, new=False):
        super().__init__(data or {})
        self.sid = sid or secrets.token_urlsafe(32)
        self.new = new
        self.old_sid = None

    def rotate(self):
        self.old_sid = self.sid
        self.sid = secrets.token_urlsafe(32)
        self.new = True


class SQLiteSessionInterface(SessionInterface):
    def db(self, app):
        app_path = Path(app.instance_path)
        app_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(app_path, 0o700)
        db_path = app_path / "sessions.sqlite3"
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(db_path, 0o600)
        db = sqlite3.connect(db_path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL)")
        return db

    def open_session(self, app, request):
        sid = request.cookies.get(self.get_cookie_name(app), "")
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", sid):
            with closing(self.db(app)) as db, db:
                row = db.execute("SELECT data FROM sessions WHERE sid=? AND expires>?", (sid, datetime.now(timezone.utc).timestamp())).fetchone()
            if row:
                try:
                    return ServerSession(json.loads(row[0]), sid=sid)
                except (ValueError, TypeError):
                    pass
        return ServerSession(new=True)

    def save_session(self, app, session, response):
        name = self.get_cookie_name(app)
        with closing(self.db(app)) as db, db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (datetime.now(timezone.utc).timestamp(),))
            if session.old_sid:
                db.execute("DELETE FROM sessions WHERE sid=?", (session.old_sid,))
            if not session:
                db.execute("DELETE FROM sessions WHERE sid=?", (session.sid,))
                response.delete_cookie(name, path=self.get_cookie_path(app), secure=self.get_cookie_secure(app), httponly=True, samesite="Lax")
                return
            expires = datetime.now(timezone.utc) + timedelta(hours=8)
            values = (json.dumps(dict(session)), expires.timestamp(), session.sid)
            if session.new:
                db.execute("INSERT INTO sessions (data, expires, sid) VALUES (?, ?, ?)", values)
            else:
                changed = db.execute("UPDATE sessions SET data=?, expires=? WHERE sid=?", values).rowcount
                if not changed:
                    response.delete_cookie(name, path=self.get_cookie_path(app), secure=self.get_cookie_secure(app), httponly=True, samesite="Lax")
                    return
        response.set_cookie(name, session.sid, expires=expires, httponly=True, secure=self.get_cookie_secure(app), samesite="Lax", path=self.get_cookie_path(app))
