import os
import secrets

from flask import Flask, redirect, url_for

from parking_demo import parking_demo
from server_session import SQLiteSessionInterface
from supabase_store import setting

app = Flask(__name__, static_folder=None)
app.secret_key = secrets.token_bytes(32)
app.session_interface = SQLiteSessionInterface()
on_render = os.getenv("RENDER", "").lower() == "true"
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=on_render or setting("SESSION_COOKIE_SECURE").lower() == "true",
                  MAX_CONTENT_LENGTH=16 * 1024)
app.register_blueprint(parking_demo)


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    if on_render:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.get("/test/parking")
def old_parking_url():
    return redirect(url_for("parking_demo.parking"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
