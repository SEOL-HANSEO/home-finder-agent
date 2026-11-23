"""Parking search with Supabase authentication and administrator audit."""
import secrets
import hashlib
import hmac
import ipaddress
import json
import os
import re
from uuid import UUID
from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import ai_assistant
from nearby import nearby_parking
from parking_provider import ParkingProviderError, provider_calls
from rate_limit import check_rate_limit
import security_agent
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


def favorite_key(place):
    return hashlib.sha256(json.dumps([place["name"], place["area"], place["lat"], place["lon"]], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def add_favorite(user, token, place):
    key = favorite_key(place)
    inserted = store.call("/rest/v1/parking_favorites?on_conflict=user_id,place_key&select=place_key", method="POST", token=token,
                          body={"user_id": user["id"], "place_key": key, **place},
                          prefer="resolution=ignore-duplicates,return=representation")
    if (not isinstance(inserted, list) or len(inserted) > 1 or
            (inserted and (not isinstance(inserted[0], dict) or inserted[0].get("place_key") != key))):
        raise store.SupabaseError()
    return {"place_key": key, "result": "added" if inserted else "already_saved"}


def favorite_feedback(place, outcome):
    added = outcome["result"] == "added"
    return {"place_key": outcome["place_key"], "place_name": place["name"], "result": outcome["result"],
            "result_label": "새로 찜했어요" if added else "이미 찜한 주차장이에요",
            "next_action": "즐겨찾기 목록 열기" if added else "기존 항목 찾아 표시",
            "choice_mode": "" if ai_assistant.configured() else "모의 선택",
            "message": f"{place['name']}을(를) 즐겨찾기에 추가했어요." if added else f"{place['name']}은(는) 이미 즐겨찾기에 있어요."}


def clear_login(user):
    store.record(user["id"] if user else None, "logout", "success")
    history, search = session.get("assistant_history", []), session.get("search_view")
    session.clear()
    if history:
        session["assistant_history"] = history
    if search:
        session["search_view"] = search


def set_administrator(actor, token, target, grant):
    if not store.is_admin(token) or not store.is_owner(token):
        abort(403)
    try:
        target = str(UUID(target.strip()))
    except (ValueError, AttributeError):
        abort(400, "올바른 사용자 ID를 입력해 주세요.")
    if not grant and target == actor["id"]:
        abort(400, "최고 관리자 본인의 권한은 해제할 수 없습니다.")
    if grant:
        store.call("/rest/v1/parking_admins?on_conflict=user_id", method="POST", token=token,
                   body={"user_id": target}, prefer="resolution=ignore-duplicates,return=minimal")
    else:
        store.call("/rest/v1/parking_admins?user_id=eq." + target, method="DELETE", token=token, prefer="return=minimal")


def select_place(items, number=0, name="", default_first=False):
    if name:
        normalized = "".join(name.split()).casefold()
        matches = [p for p in items if normalized in "".join(p.get("name", "").split()).casefold()]
        return matches[0] if len(matches) == 1 else None
    if number:
        return items[number - 1] if 1 <= number <= len(items) else None
    return items[0] if items and (default_first or len(items) == 1) else None


def explicit_removal_reference(message, number, name):
    """Avoid deleting a guessed favorite when the user did not identify it."""
    compact = "".join(message.split()).casefold()
    if re.search(r"(하지마|하지말|하지않|말아|말고)", compact):
        return False
    if name and "".join(name.split()).casefold() in compact:
        return True
    if number and (compact == str(number) or re.search(rf"(?<!\d){number}\s*번", message) or
                   (number == 1 and re.search(r"첫\s*번|첫째", message)) or
                   (number == 2 and re.search(r"두\s*번|둘째", message)) or
                   (number == 3 and re.search(r"세\s*번|셋째", message))):
        return True
    return False


def search_view(location, kind, center, places):
    return {"location": location, "place_type": kind, "center": center, "places": places}


def client_search_view(view, user):
    places = view["places"] if user else view["places"][:5]
    result = {**view, "places": []}
    for place in places:
        item = dict(place)
        if user and item.get("lat") is not None and item.get("lon") is not None:
            item["favorite_token"] = favorite_token(item)
            item["favorite_key"] = favorite_key(item)
        result["places"].append(item)
    return result


def form_token():
    return session.setdefault("parking_form_token", secrets.token_urlsafe(32))


def check_form_token():
    token = session.get("parking_form_token")
    supplied = request.form.get("form_token", "")
    if not token or not supplied.isascii() or not secrets.compare_digest(token, supplied):
        abort(403)


def consume_ai_quota(user):
    """Limit non-admin users per account or guest network, plus the shared API budget."""
    if user:
        identity = "user:" + user["id"]
        limit = 0 if store.is_admin(session["access_token"]) else 5
    else:
        address = request.remote_addr or "unknown"
        if os.getenv("RENDER", "").lower() == "true":
            forwarded = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            try:
                address = str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        identity = "guest:" + address
        limit = 3
    secret = store.setting("GEMINI_API_KEY")
    digest = hmac.new(secret.encode(), identity.encode(), hashlib.sha256).hexdigest()
    remaining = store.call("/rest/v1/rpc/parking_consume_ai", method="POST", admin=True,
                           body={"p_actor_hash": digest, "p_actor_limit": limit})
    if type(remaining) is not int:
        raise store.SupabaseError()
    return remaining


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
                    history, search = session.get("assistant_history", []), session.get("search_view")
                    pending = session.get("pending_favorite")
                    session.clear()
                    session.rotate()
                    session["access_token"] = token
                    if history:
                        session["assistant_history"] = history
                    if search:
                        session["search_view"] = search
                    if pending:
                        try:
                            outcome = add_favorite(user, token, pending)
                            session["login_favorite_feedback"] = favorite_feedback(pending, outcome)
                        except store.SupabaseError:
                            session["login_favorite_error"] = "로그인은 완료됐지만 찜 저장에 실패했어요. 다시 시도해 주세요."
                    store.record(user["id"], mode, "success")
                    return redirect(url_for(".parking", from_chat="1") if search else url_for(".parking"), code=303)
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
    if request.method == "GET" and request.args.get("from_chat") == "1":
        view = session.get("search_view")
        if view:
            values = (view["location"], view["place_type"])
            center = view["center"]
            results = view["places"] if user else view["places"][:5]
    if request.method == "POST":
        check_form_token()
        action = request.form.get("action")
        if action == "logout":
            clear_login(user)
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
            radius = request.form.get("radius", "3")
            if radius not in ("3", "5"):
                abort(400)
            check_rate_limit("search", per_session=5, total=30)
            provider_calls.set(0)
            try:
                if radius == "3":
                    results, center = nearby_parking(location, place_type, limit=None if user else 5)
                else:
                    results, center = nearby_parking(location, place_type, limit=None if user else 5, radius_km=5)
                session["search_view"] = {**search_view(location, place_type, center, results), "radius": int(radius)}
                store.record(user["id"] if user else None, "search", "success", len(results), provider_calls.get())
            except ParkingProviderError as error:
                store.record(user["id"] if user else None, "search", "failure", calls=provider_calls.get())
                message, status = str(error), 503
    if request.args.get("saved") == "1" and user and not message:
        message = "설정을 저장했습니다."
    return render_template("parking_demo.html", alias=user.get("email") if user else None,
                           user_id=user.get("id") if user else None,
                           admin=admin, values=values, place_types=PLACE_TYPES, results=results,
                           center=center, message=message, form_token=form_token(), favorite_token=favorite_token, favorite_key=favorite_key,
                           favorite_ai_configured=ai_assistant.configured(),
                           kakao_map_key=os.environ.get("KAKAO_MAP_JAVASCRIPT_KEY", "").strip(),
                           login_favorite_feedback=session.pop("login_favorite_feedback", None),
                           login_favorite_error=session.pop("login_favorite_error", None)), status


@parking_demo.route("/favorites", methods=["GET", "POST"])
def favorites():
    user = current_user()
    if not user:
        return jsonify(message="로그인 후 즐겨찾기를 사용할 수 있어요."), 401
    token = session["access_token"]
    try:
        if request.method == "GET":
            items = store.call("/rest/v1/parking_favorites?select=place_key,name,area,lat,lon,created_at&order=created_at.desc&limit=200", token=token)
            focus = request.args.get("focus", "")
            if focus:
                if not re.fullmatch(r"[0-9a-f]{64}", focus):
                    abort(400)
                if not any(item["place_key"] == focus for item in items):
                    found = store.call("/rest/v1/parking_favorites?select=place_key,name,area,lat,lon,created_at&place_key=eq." + focus + "&limit=1", token=token)
                    items = found + items
            return jsonify(favorites=items)
        check_form_token()
        action = request.form.get("action")
        if action == "add":
            place = favorite_place(request.form.get("place", ""))
            outcome = add_favorite(user, token, place)
            return jsonify(**favorite_feedback(place, outcome))
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
            remaining = consume_ai_quota(user)
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
        outcome = add_favorite(user, session["access_token"], place)
    except store.SupabaseError:
        return jsonify(message="즐겨찾기를 저장하지 못했습니다.", mode=mode, remaining=remaining), 503
    return jsonify(**favorite_feedback(place, outcome), mode=mode, remaining=remaining)


@parking_demo.route("/admin", methods=["GET", "POST"])
def admin():
    user = current_user()
    if not user:
        abort(401)
    error = ""
    status = 200
    try:
        token = session["access_token"]
        if not store.is_admin(token):
            abort(403)
        owner = store.is_owner(token)
        if request.method == "POST":
            check_form_token()
            action = request.form.get("action")
            if action not in ("grant_admin", "revoke_admin"):
                abort(400)
            if not owner:
                abort(403)
            target = request.form.get("user_id", "").strip()
            if action == "grant_admin" and request.form.get("email") is not None:
                email = request.form.get("email", "").strip()
                if not (3 <= len(email) <= 254 and "@" in email and not any(c.isspace() for c in email)):
                    error, status = "올바른 가입 이메일을 입력해 주세요.", 400
                else:
                    target = store.user_id_by_email(email)
                    if not target:
                        error, status = "가입된 계정을 찾지 못했습니다. 이메일을 확인해 주세요.", 404
            if not error:
                set_administrator(user, token, target, action == "grant_admin")
                return redirect(url_for(".admin", updated="1"), code=303)
        logs = store.call("/rest/v1/parking_audit?select=created_at,actor_alias,action,outcome,result_count,provider_calls&order=created_at.desc&limit=100", token=token)
        findings = security_agent.analyze(logs)
        for finding in findings:
            try:
                store.call("/rest/v1/parking_security_reports?on_conflict=fingerprint", method="POST", admin=True,
                           body=finding, prefer="resolution=ignore-duplicates,return=minimal")
            except store.SupabaseError:
                current_app.logger.warning("보안 분석 기록 저장에 실패했습니다.")
        security_reports = store.call("/rest/v1/parking_security_reports?select=recorded_at,observed_at,actor_alias,severity,rule,summary&order=recorded_at.desc&limit=50", admin=True)
        changes = store.call("/rest/v1/parking_admin_activity?select=occurred_at,actor_id,parking_lot_id,operation&order=occurred_at.desc&limit=100", token=token)
        favorite_records = store.call("/rest/v1/parking_favorites?select=name,area,created_at&order=created_at.desc&limit=1000", admin=True)
        favorite_totals = {}
        for item in favorite_records:
            key = (item.get("name", ""), item.get("area", ""))
            if not all(key):
                continue
            entry = favorite_totals.setdefault(key, {"name": key[0], "area": key[1], "count": 0, "latest": item.get("created_at", "")})
            entry["count"] += 1
        favorite_ranking = sorted(favorite_totals.values(), key=lambda item: item["count"], reverse=True)[:10]
        search_summary = {
            "success": sum(1 for item in logs if item.get("action") == "search" and item.get("outcome") == "success"),
            "failure": sum(1 for item in logs if item.get("action") == "search" and item.get("outcome") == "failure"),
        }
        admins = store.call("/rest/v1/parking_admins?select=user_id&order=user_id.asc", token=token) if owner else []
    except store.SupabaseError:
        abort(503, "기록을 불러오지 못했습니다.")
    return render_template("admin.html", logs=logs, changes=changes, error=error,
                           favorite_ranking=favorite_ranking, search_summary=search_summary,
                           security_reports=security_reports,
                           form_token=form_token(), updated=request.args.get("updated") == "1",
                           owner=owner, admins=admins, owner_id=user["id"]), status


@parking_demo.post("/assistant")
def assistant():
    check_form_token()
    user = current_user()
    message = request.form.get("message", "").strip()
    if not message or len(message) > 500:
        return jsonify(message="메시지를 1자 이상 500자 이하로 입력해 주세요."), 400
    if not ai_assistant.configured():
        return jsonify(message="AI 서비스가 아직 설정되지 않았습니다. 관리자에게 문의해 주세요."), 503
    check_rate_limit("search", per_session=5, total=30)
    try:
        remaining = consume_ai_quota(user)
    except store.SupabaseError:
        return jsonify(message="이용 권한을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."), 503
    if remaining < 0:
        text = "오늘 서비스의 전체 또는 일반 사용자 AI 이용 한도에 도달했어요. 나중에 다시 이용해 주세요." if remaining == -2 else "오늘의 AI 이용 횟수를 모두 사용했어요. 내일 다시 이용해 주세요."
        return jsonify(message=text), 429
    try:
        history = session.get("assistant_history", [])
        previous_view = session.get("search_view") or {}
        context_places = previous_view.get("places", [])
        if not user:
            context_places = context_places[:5]
        answer = ai_assistant.respond(message, history, context_places, authenticated=bool(user))
    except ai_assistant.AssistantError as error:
        return jsonify(message=str(error), remaining=remaining), 503
    if (answer["action"] == "favorite_add" and not context_places
            and answer.get("location")):
        # A combined find-and-save request can be classified as favorite_add even
        # though the assistant has no current result list yet. Search first.
        answer["action"] = "search"
        answer["favorite_after_search"] = True
    if answer["action"] == "reply":
        session["assistant_history"] = (history + [{"role": "user", "text": message},
                                        {"role": "model", "text": answer["reply"]}])[-6:]
        return jsonify(message=answer["reply"], places=[], remaining=remaining)
    if answer["action"] != "search":
        action = answer["action"]
        extra = {}
        try:
            if action == "favorite_add" and not user:
                place = select_place(context_places, answer.get("number", 0), answer.get("target_name", ""), default_first=True)
                if not place:
                    reply = "먼저 주차장을 검색하고 찜할 장소를 알려주세요."
                elif re.search(r"(하지\s*마|하지\s*말|말고|취소)", message):
                    reply = "즐겨찾기를 추가하지 않았어요."
                else:
                    session["pending_favorite"] = {key: place[key] for key in ("name", "area", "lat", "lon")}
                    reply = f"{place['name']}은 로그인하면 자동으로 찜할게요."
                    extra["navigate"] = url_for(".account", mode="login")
            elif action in ("favorite_remove", "favorite_view", "save_preferences", "admin_grant", "admin_revoke") and not user:
                reply = "이 기능은 로그인 후 사용할 수 있어요. 로그인한 뒤 다시 말씀해 주세요."
            elif action == "favorite_add":
                places = previous_view.get("places", [])
                place = select_place(places, answer.get("number", 0), answer.get("target_name", ""), default_first=True)
                if re.search(r"(하지\s*마|하지\s*말|말고|취소)", message):
                    reply = "즐겨찾기를 추가하지 않았어요."
                elif not place:
                    reply = "먼저 주차장을 검색하고, 저장할 주차장 이름이나 번호를 알려주세요."
                else:
                    favorite = {key: place[key] for key in ("name", "area", "lat", "lon")}
                    outcome = add_favorite(user, session["access_token"], favorite)
                    feedback = favorite_feedback(favorite, outcome)
                    reply = f"{feedback['place_name']} · 첫 결과: {feedback['result_label']} · 선택한 행동: {feedback['next_action']}"
                    extra["favorites_changed"] = True
                    extra["favorite_feedback"] = feedback
            elif action == "favorite_remove":
                items = store.call("/rest/v1/parking_favorites?select=place_key,name,area,lat,lon,created_at&order=created_at.desc&limit=200", token=session["access_token"])
                number, name = answer.get("number", 0), answer.get("target_name", "")
                negated = re.search(r"(하지\s*(마|말|않)|말아|말고|하지는\s*마)", message)
                if negated:
                    reply = "즐겨찾기를 그대로 두었어요."
                elif not items:
                    reply = "해제할 즐겨찾기가 없어요."
                elif not (place := (select_place(items, number, name) if explicit_removal_reference(message, number, name)
                                    else items[0] if len(items) == 1 and not number and not name else None)):
                    reply = "어느 즐겨찾기를 해제할까요? 번호나 이름을 알려주세요.\n" + "\n".join(
                        f"{i}. {item['name']}" for i, item in enumerate(items[:20], 1))
                else:
                    store.call("/rest/v1/parking_favorites?place_key=eq." + place["place_key"], method="DELETE", token=session["access_token"], prefer="return=minimal")
                    reply = f"{place['name']}을(를) 즐겨찾기에서 삭제했어요."
                    extra["favorites_changed"] = True
            elif action == "favorite_view":
                reply = "즐겨찾기 목록을 보여드릴게요."
                extra["open_favorites"] = True
            elif action == "save_preferences":
                location = answer.get("location") or previous_view.get("location", "")
                same_location = "".join(location.split()) == "".join(previous_view.get("location", "").split())
                kind = previous_view.get("place_type", "all") if same_location else answer.get("place_type", "all")
                if not location or len(location) > 100 or kind not in PLACE_TYPES:
                    reply = "기본 검색으로 저장할 지역을 먼저 알려주세요."
                else:
                    store.save_preferences(session["access_token"], user["id"], location, kind)
                    store.record(user["id"], "save", "success")
                    reply = f"{location}을(를) 기본 검색 위치로 저장했어요."
                    extra["preferences"] = {"location": location, "place_type": kind}
            elif action == "theme":
                theme = answer.get("theme")
                if theme not in ("dark", "light"):
                    reply = "다크 모드와 화이트 모드 중 어떤 화면을 원하세요?"
                else:
                    extra["theme"] = theme
                    reply = "다크 모드로 바꿨어요." if theme == "dark" else "화이트 모드로 바꿨어요."
            elif action == "map_select":
                place = select_place(context_places, answer.get("number", 0), answer.get("target_name", ""), default_first=True)
                if not place or place.get("lat") is None or place.get("lon") is None:
                    reply = "지도에서 볼 주차장을 먼저 검색하고 이름이나 번호를 알려주세요."
                else:
                    extra["map_index"] = context_places.index(place)
                    reply = f"지도에서 {place['name']} 위치를 보여드릴게요."
            elif action == "map_zoom":
                direction = answer.get("direction")
                if direction in ("in", "out"):
                    extra["map_zoom"] = direction
                    reply = "지도를 확대했어요." if direction == "in" else "지도를 축소했어요."
                else:
                    reply = "지도를 확대할까요, 축소할까요?"
            elif action == "guide":
                direction = answer.get("direction")
                if direction in ("next", "previous", "play"):
                    extra["guide"] = direction
                    reply = "서비스 안내를 조작할게요."
                else:
                    reply = "서비스 안내를 재생하거나 이전·다음 단계로 이동할 수 있어요."
            elif action == "open_source":
                extra["link"] = "https://www.data.go.kr/data/15012896/standard.do"
                reply = "주차장 데이터 출처를 열 수 있는 링크예요."
            elif action == "navigate":
                page = answer.get("page")
                if page == "login" and user:
                    reply = "이미 로그인되어 있어요. 원하시는 기능을 말씀해 주세요."
                elif page == "admin" and (not user or not store.is_admin(session["access_token"])):
                    reply = "관리자 화면은 관리자 계정으로 로그인해야 열 수 있어요."
                elif page in ("home", "login", "signup", "admin"):
                    extra["navigate"] = (url_for(".parking", from_chat="1") if page == "home" else
                                         url_for(".admin") if page == "admin" else url_for(".account", mode=page))
                    reply = {"home": "검색 화면으로 이동할게요.", "login": "로그인 화면으로 이동할게요.", "signup": "회원가입 화면으로 이동할게요.", "admin": "관리자 화면으로 이동할게요."}[page]
                else:
                    reply = "어느 화면으로 이동할까요?"
            elif action == "logout":
                if not re.search(r"(로그\s*아웃|sign\s*out|log\s*out)", message, re.I) or re.search(r"(하지\s*마|하지\s*말|말고|취소)", message):
                    reply = "로그아웃을 원하시면 말씀해 주세요."
                else:
                    reply = "로그아웃했어요." if user else "이미 로그아웃되어 있어요."
                    extra["navigate"] = url_for(".parking", from_chat="1")
            elif action in ("admin_grant", "admin_revoke"):
                target = (answer.get("email") or answer.get("user_id", "")).strip()
                if not store.is_owner(session["access_token"]):
                    reply = "관리자 권한은 최고 관리자만 변경할 수 있어요."
                elif not target or target.casefold() not in message.casefold():
                    reply = "권한을 변경할 사용자의 가입 이메일을 메시지에 직접 적어 주세요."
                elif re.search(r"(하지\s*마|하지\s*말|말고|취소|금지)", message) or not re.search(r"(관리자|admin)", message, re.I) or not re.search(r"(부여|추가|지정|줘|해제|회수|제거|삭제)", message):
                    reply = "관리자 권한을 부여할지 해제할지 명확히 말씀해 주세요."
                elif action == "admin_grant" and re.search(r"(해제|회수|제거|삭제)", message):
                    reply = "관리자 권한 변경 요청을 다시 확인해 주세요."
                elif action == "admin_revoke" and not re.search(r"(해제|회수|제거|삭제)", message):
                    reply = "관리자 권한 변경 요청을 다시 확인해 주세요."
                else:
                    if "@" in target:
                        target = store.user_id_by_email(target)
                        if not target:
                            reply = "가입된 계정을 찾지 못했어요. 이메일을 확인해 주세요."
                            session["assistant_history"] = (history + [{"role": "user", "text": message}, {"role": "model", "text": reply}])[-6:]
                            return jsonify(message=reply, remaining=remaining)
                    try:
                        target = str(UUID(target))
                    except ValueError:
                        reply = "사용자 ID 형식을 확인해 주세요."
                    else:
                        if action == "admin_revoke" and target == user["id"]:
                            reply = "최고 관리자 본인의 권한은 해제할 수 없어요."
                        else:
                            set_administrator(user, session["access_token"], target, action == "admin_grant")
                            reply = "관리자 권한을 부여했어요." if action == "admin_grant" else "관리자 권한을 해제했어요."
                            extra["admin_changed"] = True
            else:
                reply = "요청을 처리하지 못했어요. 다시 말씀해 주세요."
        except (store.SupabaseError, KeyError, TypeError):
            return jsonify(message="요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.", remaining=remaining), 503
        session["assistant_history"] = (history + [{"role": "user", "text": message}, {"role": "model", "text": reply}])[-6:]
        if action == "logout" and user and extra.get("navigate"):
            clear_login(user)
        return jsonify(message=reply, remaining=remaining, **extra)
    location, kind = answer["location"], answer["place_type"]
    provider_calls.set(0)
    try:
        places, center = nearby_parking(location, kind, limit=None if user else 5)
        view = {**search_view(location, kind, center, places), "radius": 3}
        session["search_view"] = view
        store.record(user["id"] if user else None, "search", "success", len(places), provider_calls.get())
    except ParkingProviderError as error:
        store.record(user["id"] if user else None, "search", "failure", calls=provider_calls.get())
        return jsonify(message=str(error), remaining=remaining), 503
    extra = {}
    favorite_requested = answer.get("favorite_after_search", False)
    canceled = re.search(r"(하지\s*마|하지\s*말|말고|취소)", message)
    if favorite_requested and places and not canceled:
        place = select_place(places, answer.get("number", 0), answer.get("target_name", ""), default_first=True)
        if not place:
            reply = f"'{location}'에서 주차장을 찾았어요. 찜할 주차장 이름이나 번호를 알려주세요."
        elif user:
            favorite = {key: place[key] for key in ("name", "area", "lat", "lon")}
            try:
                outcome = add_favorite(user, session["access_token"], favorite)
                feedback = favorite_feedback(favorite, outcome)
                reply = f"'{location}'에서 찾은 주차장이에요. {feedback['place_name']} · {feedback['result_label']}"
                extra["favorites_changed"] = True
                extra["favorite_feedback"] = feedback
            except store.SupabaseError:
                reply = f"'{location}'에서 주차장은 찾았지만 찜 저장에 실패했어요. 다시 시도해 주세요."
        else:
            session["pending_favorite"] = {key: place[key] for key in ("name", "area", "lat", "lon")}
            reply = f"'{location}'에서 찾은 {place['name']}을(를) 찜할게요. 로그인하면 자동으로 저장됩니다."
            extra["navigate"] = url_for(".account", mode="login")
    elif favorite_requested and canceled and places:
        reply = f"'{location}'에서 주차장을 찾았어요. 요청하신 대로 즐겨찾기는 추가하지 않았습니다."
    elif not places:
        reply = f"'{location}'에서 3km 이내에 조건에 맞는 무료 주차장을 찾지 못했어요. 다른 위치나 유형으로 검색해 보세요."
    else:
        reply = f"'{location}'에서 가까운 무료 주차장이에요. 직선거리 기준이며 실제 이용 가능 여부는 현장에서 확인해 주세요."
    session["assistant_history"] = (history + [{"role": "user", "text": message},
                                    {"role": "model", "text": reply}])[-6:]
    return jsonify(message=reply, places=places[:3], remaining=remaining, search=client_search_view(view, user), **extra)
