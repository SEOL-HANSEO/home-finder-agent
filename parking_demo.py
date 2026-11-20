"""Parking search with Supabase authentication and administrator audit."""
import secrets
from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from nearby import nearby_parking
from parking_provider import ParkingProviderError, provider_calls
from rate_limit import check_rate_limit
import supabase_store as store

parking_demo = Blueprint("parking_demo", __name__)
PLACE_TYPES = {"all": "전체", "off_street": "노외 주차장", "on_street": "노상 주차장"}


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
                           admin=admin, values=values, place_types=PLACE_TYPES, results=results,
                           center=center, message=message, form_token=form_token()), status


@parking_demo.get("/admin")
def admin():
    user = current_user()
    if not user:
        abort(401)
    try:
        token = session["access_token"]
        if not store.is_admin(token):
            abort(403)
        logs = store.call("/rest/v1/parking_audit?select=created_at,actor_alias,action,outcome,result_count,provider_calls&order=created_at.desc&limit=100", token=token)
        changes = store.call("/rest/v1/parking_admin_activity?select=occurred_at,actor_id,parking_lot_id,operation&order=occurred_at.desc&limit=100", token=token)
    except store.SupabaseError:
        abort(503, "기록을 불러오지 못했습니다.")
    return render_template("admin.html", logs=logs, changes=changes)
