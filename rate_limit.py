"""Bound expensive requests on the single-instance deployment."""
from contextlib import closing
import time

from flask import abort, current_app, session


def check_rate_limit(kind, *, per_session, total, window=60):
    now = time.time()
    interface = current_app.session_interface
    with closing(interface.db(current_app)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS request_limits (kind TEXT NOT NULL, sid TEXT NOT NULL, created REAL NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM request_limits WHERE created <= ?", (now - window,))
        all_count, session_count = db.execute(
            "SELECT count(*), count(CASE WHEN sid = ? THEN 1 END) FROM request_limits WHERE kind = ?",
            (session.sid, kind),
        ).fetchone()
        if all_count >= total or session_count >= per_session:
            db.commit()
            abort(429, "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
        db.execute("INSERT INTO request_limits (kind, sid, created) VALUES (?, ?, ?)", (kind, session.sid, now))
        db.commit()
