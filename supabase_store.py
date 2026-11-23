"""Supabase Auth and data API calls. Tokens remain on the server."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SupabaseError(Exception):
    def __init__(self, status=None):
        self.status = status


def setting(name):
    value = os.getenv(name, "").strip()
    if value:
        return value
    path = Path(__file__).with_name(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key == name:
                return value.strip()
    return ""


def call(path, *, method="GET", body=None, token=None, admin=False, prefer=None):
    url = setting("SUPABASE_URL").rstrip("/")
    key = setting("SUPABASE_SECRET_KEY" if admin else "SUPABASE_PUBLISHABLE_KEY")
    if not url.startswith("https://") or not key:
        raise SupabaseError()
    headers = {"apikey": key, "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    request = Request(url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read()
        return json.loads(payload) if payload else None
    except HTTPError as error:
        raise SupabaseError(error.code) from None
    except (URLError, OSError, ValueError):
        raise SupabaseError() from None


def get_user(token):
    return call("/auth/v1/user", token=token)


def is_admin(token):
    result = call("/rest/v1/parking_admins?select=user_id&limit=1", token=token)
    return bool(result)


def is_owner(token):
    result = call("/rest/v1/parking_owner?select=user_id&limit=1", token=token)
    return bool(result)


def user_id_by_email(email):
    """Look up an exact email on the server after the caller checks owner rights."""
    matches = []
    for page in range(1, 101):
        data = call(f"/auth/v1/admin/users?page={page}&per_page=1000", admin=True)
        users = data.get("users") if isinstance(data, dict) else None
        if not isinstance(users, list):
            raise SupabaseError()
        matches.extend(u.get("id") for u in users if isinstance(u, dict) and isinstance(u.get("email"), str)
                       and u["email"].casefold() == email.casefold() and u.get("id"))
        if len(users) < 1000:
            if len(matches) > 1:
                raise SupabaseError()
            return matches[0] if matches else None
    raise SupabaseError()


def preferences(token):
    return call("/rest/v1/parking_preferences?select=location,place_type&limit=1", token=token)


def save_preferences(token, user_id, location, place_type):
    return call("/rest/v1/parking_preferences?on_conflict=user_id", method="POST", token=token,
                body={"user_id": user_id, "location": location, "place_type": place_type},
                prefer="resolution=merge-duplicates,return=minimal")


def record(user_id, action, outcome, count=0, calls=0):
    try:
        call("/rest/v1/parking_audit", method="POST", admin=True,
             body={"actor_alias": str(user_id)[:8] if user_id else "guest",
                   "action": action, "outcome": outcome, "result_count": count, "provider_calls": calls},
             prefer="return=minimal")
        return True
    except SupabaseError:
        from flask import current_app
        current_app.logger.warning("활동 기록 저장에 실패했습니다.")
        return False
