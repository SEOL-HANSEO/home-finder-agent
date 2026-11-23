"""Parking search with Supabase authentication and administrator audit."""
import secrets
import hashlib
import json
import re
from uuid import UUID
from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import ai_assistant
from nearby import nearby_parking
from parking_provider import ParkingProviderError, provider_calls
from rate_limit import check_rate_limit
import supabase_store as store

parking_demo = Blueprint("parking_demo", __name__)
PLACE_TYPES = {"all": "전체", "off_street": "노외 주차장", "on_street": "노상 주차장"}


def place_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="parking-favorite")


def favorite_token(place):
    return place_serializer().dumps({key: place[key] for key in ("name", "area", "lat", "lon")})


def favorite_place(token):
    try:
        place = place_serializer().loads(token, max_age=3600)
        if (not isinstance(place, dict) or not isinstance(place.get("name"), str)
                or not 1 <= len(place["name"]) <= 200 or not isinstance(place.get("area"), str)
                or not 1 <= len(place["area"]) <= 300 or not isinstance(place.get("lat"), (int, float))
                or not isinstance(place.get("lon"), (int, float))
                or not 33 <= place["lat"] <= 39 or not 124 <= place["lon"] <= 132):
            raise ValueError()
        return place
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        abort(400, "검색 결과가 만료됐습니다. 다시 검색해 주세요.")


def add_favorite(user, token, place):
    key = hashlib.sha256(json.dumps([place["name"], place["area"], place["lat"], place["lon"]], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    store.call("/rest/v1/parking_favorites?on_conflict=user_id,place_key", method="POST", token=token,
               body={"user_id": user["id"], "place_key": key, **place},
               prefer="resolution=ignore-duplicates,return=minimal")
    return key


def form_token():
    return session.setdefault("parking_form_token", secrets.token_urlsafe(32))


def check_form_token():
    token = session.get("parking_form_token")
    supplied = request.form.get("form_token", "")
    if not token or not supplied.isascii() or not secrets.compare_digest(token, supplied):
        abort(403)


def current_user():
    token = session.get("access_token")
    if not token:
        return None
    try:
        return store.get_user(token)
    except store.SupabaseError as error:
        if error.status in (401, 403):
            session.clear()
            return None
        abort(503, "로그인 상태를 확인하지 못했습니다.")


@parking_demo.after_request
def prevent_caching(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@parking_demo.route("/account/<mode>", methods=["GET", "POST"])
def account(mode):
    if mode not in ("login", "signup"):
        abort(404)
    error = ""
    notice = ""
    status = 200
    if request.method == "POST":
        check_form_token()
        check_rate_limit("account", per_session=10, total=100)
        email = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not (3 <= len(email) <= 254 and "@" in email and 8 <= len(password) <= 128):
            error, status = "이메일과 8자 이상의 비밀번호를 입력해 주세요.", 400
        else:
            try:
                path = "/auth/v1/signup" if mode == "signup" else "/auth/v1/token?grant_type=password"
                data = store.call(path, method="POST", body={"email": email, "password": password})
                token = data.get("access_token")
                if token:
                    user = store.get_user(token)
                    session.clear()
                    session.rotate()
                    session["access_token"] = token
                    store.record(user["id"], mode, "success")
                    return redirect(url_for(".parking"), code=303)
                if mode == "signup":
                    store.record(None, "signup", "success")
                    notice = "가입 확인 이메일을 확인한 뒤 로그인해 주세요."
                else:
                    raise store.SupabaseError()
            except store.SupabaseError as exc:
                store.record(None, mode, "failure")
                if exc.status == 429:
                    if mode == "signup":
                        error = "인증 메일 발송 또는 가입 요청이 너무 많습니다. 잠시 기다린 뒤 다시 시도해 주세요."
                    else:
                        error = "로그인 요청이 너무 많습니다. 잠시 기다린 뒤 다시 시도해 주세요."
                    status = 429
                elif exc.status in (400, 401, 422):
                    error, status = "이메일이나 비밀번호를 확인해 주세요.", 400
                else:
                    error, status = "인증 서비스를 사용할 수 없습니다. 설정과 연결 상태를 확인해 주세요.", 503
    return render_template("account.html", mode=mode, error=error, notice=notice, form_token=form_token()), status


@parking_demo.route("/", methods=["GET", "POST"])
def parking():
    user = current_user()
    token = session.get("access_token")
    results = None
    center = None
    message = ""
    status = 200
    values = ("", "all")
    admin = False
    if user:
        try:
            saved = store.preferences(token)
            if saved:
                values = (saved[0]["location"], saved[0]["place_type"])
            admin = store.is_admin(token)
        except store.SupabaseError:
            abort(503, "계정 정보를 불러오지 못했습니다.")
    if request.method == "POST":
        check_form_token()
        action = request.form.get("action")
        if action == "logout":
            store.record(user["id"] if user else None, "logout", "success")
            session.clear()
            return redirect(url_for(".parking"), code=303)
        if action not in ("search", "save"):
            abort(400)
        if action == "save" and not user:
            store.record(None, "save", "denied")
            abort(401)
        location = request.form.get("location", "").strip()
        place_type = request.form.get("place_type", "")
        if not location or len(location) > 100 or place_type not in PLACE_TYPES:
            message, status = "검색 위치와 장소 종류를 확인해 주세요.", 400
        elif action == "save":
            try:
                store.save_preferences(token, user["id"], location, place_type)
                store.record(user["id"], "save", "success")
                return redirect(url_for(".parking", saved="1"), code=303)
            except store.SupabaseError:
                store.record(user["id"], "save", "failure")
                message, status = "설정을 저장하지 못했습니다.", 503
        else:
            values = (location, place_type)
            check_rate_limit("search", per_session=5, total=30)
            provider_calls.set(0)
            try:
                results, center = nearby_parking(location, place_type, limit=None if user else 5)
                store.record(user["id"] if user else None, "search", "success", len(results), provider_calls.get())
            except ParkingProviderError as error:
                store.record(user["id"] if user else None, "search", "failure", calls=provider_calls.get())
                message, status = str(error), 503
    if request.args.get("saved") == "1" and user and not message:
        message = "설정을 저장했습니다."
    return render_template("parking_demo.html", alias=user.get("email") if user else None,
                           user_id=user.get("id") if user else None,
                           admin=admin, values=values, place_types=PLACE_TYPES, results=results,
                           center=center, message=message, form_token=form_token(), favorite_token=favorite_token,
                           favorite_ai_configured=ai_assistant.configured()), status


@parking_demo.route("/favorites", methods=["GET", "POST"])
def favorites():
    user = current_user()
    if not user:
        return jsonify(message="로그인 후 즐겨찾기를 사용할 수 있어요."), 401
    token = session["access_token"]
    try:
        if request.method == "GET":
            items = store.call("/rest/v1/parking_favorites?select=place_key,name,area,lat,lon,created_at&order=created_at.desc&limit=200", token=token)
            return jsonify(favorites=items)
        check_form_token()
        action = request.form.get("action")
        if action == "add":
            place = favorite_place(request.form.get("place", ""))
            add_favorite(user, token, place)
            return jsonify(message=f"{place['name']}을(를) 즐겨찾기에 추가했습니다.")
        if action == "delete":
            key = request.form.get("place_key", "")
            if not re.fullmatch(r"[0-9a-f]{64}", key):
                abort(400)
            store.call("/rest/v1/parking_favorites?place_key=eq." + key, method="DELETE", token=token, prefer="return=minimal")
            return jsonify(message="즐겨찾기에서 삭제했습니다.")
        abort(400)
    except store.SupabaseError:
        return jsonify(message="즐겨찾기를 저장하거나 불러오지 못했습니다."), 503


@parking_demo.post("/favorites/command")
def favorite_command():
    check_form_token()
    user = current_user()
    if not user:
        return jsonify(message="로그인 후 즐겨찾기를 사용할 수 있어요."), 401
    command = request.form.get("command", "").strip()
    tokens = request.form.getlist("place")
    if not command or len(command) > 200 or not tokens or len(tokens) > 30:
        return jsonify(message="검색 후 '1번 즐겨찾기 추가'처럼 입력해 주세요."), 400
    places = [favorite_place(token) for token in tokens]
    check_rate_limit("favorite_command", per_session=10, total=100)
    mode = "모의 선택"
    remaining = None
    if ai_assistant.configured():
        try:
            remaining = store.call("/rest/v1/rpc/parking_use_chat", method="POST", admin=True, body={"p_user_id": user["id"]})
        except store.SupabaseError:
            return jsonify(message="AI 사용 권한을 확인하지 못했습니다."), 503
        if type(remaining) is not int:
            return jsonify(message="AI 사용 권한을 확인하지 못했습니다."), 503
    if remaining is not None and remaining >= 0:
        mode = "AI 선택"
        try:
            number = ai_assistant.select_favorite(command, places)
        except ai_assistant.AssistantError as error:
            return jsonify(message=str(error), mode=mode, remaining=remaining), 503
    else:
        match = re.fullmatch(r"\s*(\d{1,2})\s*번\s*(?:을|를)?\s*즐겨찾기(?:에)?\s*(?:추가|저장)(?:해\s*줘|해주세요)?\s*[.!]?\s*", command)
        number = int(match.group(1)) if match else 0
    if not 1 <= number <= len(places):
        return jsonify(message="저장할 항목을 찾지 못했어요. 예: 1번 즐겨찾기 추가", mode=mode, remaining=remaining), 400
    try:
        place = places[number - 1]
        add_favorite(user, session["access_token"], place)
    except store.SupabaseError:
        return jsonify(message="즐겨찾기를 저장하지 못했습니다.", mode=mode, remaining=remaining), 503
    return jsonify(message=f"{place['name']}을(를) 즐겨찾기에 추가했습니다.", mode=mode, remaining=remaining)


@parking_demo.route("/admin", methods=["GET", "POST"])
def admin():
    user = current_user()
    if not user:
        abort(401)
    try:
        token = session["access_token"]
        if not store.is_admin(token):
            abort(403)
        owner = store.is_owner(token)
        if request.method == "POST":
            check_form_token()
            action = request.form.get("action")
            if action not in ("grant_chat", "revoke_chat", "grant_admin", "revoke_admin"):
                abort(400)
            if action in ("grant_admin", "revoke_admin") and not owner:
                abort(403)
            try:
                target = str(UUID(request.form.get("user_id", "").strip()))
            except ValueError:
                abort(400, "올바른 사용자 ID를 입력해 주세요.")
            if action == "grant_admin":
                store.call("/rest/v1/parking_admins?on_conflict=user_id", method="POST", token=token,
                           body={"user_id": target}, prefer="resolution=ignore-duplicates,return=minimal")
            elif action == "revoke_admin":
                if target == user["id"]:
                    abort(400, "최고 관리자 본인의 권한은 해제할 수 없습니다.")
                store.call("/rest/v1/parking_admins?user_id=eq." + target, method="DELETE", token=token,
                           prefer="return=minimal")
            elif action == "grant_chat":
                store.call("/rest/v1/parking_chat_access?on_conflict=user_id", method="POST", admin=True,
                           body={"user_id": target, "granted_by": user["id"]},
                           prefer="resolution=ignore-duplicates,return=minimal")
            else:
                store.call("/rest/v1/parking_chat_access?user_id=eq." + target, method="DELETE", admin=True,
                           prefer="return=minimal")
            return redirect(url_for(".admin", updated="1"), code=303)
        logs = store.call("/rest/v1/parking_audit?select=created_at,actor_alias,action,outcome,result_count,provider_calls&order=created_at.desc&limit=100", token=token)
        changes = store.call("/rest/v1/parking_admin_activity?select=occurred_at,actor_id,parking_lot_id,operation&order=occurred_at.desc&limit=100", token=token)
        admins = store.call("/rest/v1/parking_admins?select=user_id&order=user_id.asc", token=token) if owner else []
    except store.SupabaseError:
        abort(503, "기록을 불러오지 못했습니다.")
    try:
        chat_access = store.call("/rest/v1/parking_chat_access?select=user_id,granted_at&order=granted_at.desc&limit=100", admin=True)
    except store.SupabaseError:
        chat_access = None
    return render_template("admin.html", logs=logs, changes=changes, chat_access=chat_access,
                           form_token=form_token(), updated=request.args.get("updated") == "1",
                           owner=owner, admins=admins, owner_id=user["id"])


@parking_demo.post("/assistant")
def assistant():
    check_form_token()
    user = current_user()
    if not user:
        return jsonify(message="로그인 후 사용할 수 있어요."), 401
    message = request.form.get("message", "").strip()
    if not message or len(message) > 500:
        return jsonify(message="메시지를 1자 이상 500자 이하로 입력해 주세요."), 400
    if not ai_assistant.configured():
        return jsonify(message="AI 서비스가 아직 설정되지 않았습니다. 관리자에게 문의해 주세요."), 503
    check_rate_limit("search", per_session=5, total=30)
    try:
        remaining = store.call("/rest/v1/rpc/parking_use_chat", method="POST", admin=True,
                               body={"p_user_id": user["id"]})
    except store.SupabaseError:
        return jsonify(message="이용 권한을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."), 503
    if not isinstance(remaining, int) or isinstance(remaining, bool):
        return jsonify(message="이용 권한을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."), 503
    if remaining < 0:
        return jsonify(message="이용 권한이 없거나 오늘의 3회 이용 한도를 모두 사용했어요. 관리자에게 문의하거나 내일 다시 이용해 주세요."), 403
    try:
        history = session.get("assistant_history", [])
        answer = ai_assistant.respond(message, history)
    except ai_assistant.AssistantError as error:
        return jsonify(message=str(error), remaining=remaining), 503
    if answer["action"] == "reply":
        session["assistant_history"] = (history + [{"role": "user", "text": message},
                                        {"role": "model", "text": answer["reply"]}])[-6:]
        return jsonify(message=answer["reply"], places=[], remaining=remaining)
    location, kind = answer["location"], answer["place_type"]
    provider_calls.set(0)
    try:
        places, _ = nearby_parking(location, kind, limit=3)
        store.record(user["id"] if user else None, "search", "success", len(places), provider_calls.get())
    except ParkingProviderError as error:
        store.record(user["id"] if user else None, "search", "failure", calls=provider_calls.get())
        return jsonify(message=str(error), remaining=remaining), 503
    if not places:
        reply = f"'{location}'에서 3km 이내에 조건에 맞는 무료 주차장을 찾지 못했어요. 다른 위치나 유형으로 검색해 보세요."
    else:
        reply = f"'{location}'에서 가까운 무료 주차장이에요. 직선거리 기준이며 실제 이용 가능 여부는 현장에서 확인해 주세요."
    session["assistant_history"] = (history + [{"role": "user", "text": message},
                                    {"role": "model", "text": reply}])[-6:]
    return jsonify(message=reply, places=places, remaining=remaining)
