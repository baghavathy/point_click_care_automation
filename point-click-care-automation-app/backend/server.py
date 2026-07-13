"""Gateway PCC — CLOUD server (hosted at e.g. pcc.arithmed.com).

This is the *thin* half of the system. It does NOT run any browser. It:
  * authenticates users (browser via session cookie, desktop via bearer token),
  * lets an admin create users and manage clients/facilities,
  * stores PCC credentials/TOTP secrets ENCRYPTED at rest, and
  * hands a facility's decrypted secrets to the signed-in desktop agent, over
    HTTPS, only at launch time (`/api/facilities/<id>/launch-config`).

All the heavy lifting (Firefox, proxy, auto-filling username/password/OTP) runs
on the user's desktop — see the separate ``gateway-desktop`` app.
"""
from __future__ import annotations

import functools

from flask import Flask, g, jsonify, render_template, request, session

from . import auth, config, database, qrtools

app = Flask(
    __name__,
    template_folder=str(config.TEMPLATES_DIR),
    static_folder=str(config.STATIC_DIR),
)


def _load_secret() -> bytes:
    """Flask session signing secret, from FLASK_SECRET_KEY in .env.

    If unset, fall back to a per-process random key — usable for dev, but it
    signs everyone out on restart, so set FLASK_SECRET_KEY in .env in production.
    """
    if config.FLASK_SECRET_KEY:
        return config.FLASK_SECRET_KEY.encode("utf-8")
    import os

    return os.urandom(32)


app.secret_key = _load_secret()


# --------------------------------------------------------------------------
# Auth helpers — accept EITHER a bearer token (desktop) OR a session (browser)
# --------------------------------------------------------------------------
def _user_from_request():
    """Resolve the caller from an Authorization bearer token or the session.

    Returns a dict {id, username, role} or None. Cached on ``g`` per request.
    """
    if "auth_user" in g.__dict__:
        return g.auth_user
    user = None
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        user = auth.read_token(app.secret_key, header[7:].strip())
    if user is None and session.get("uid") is not None:
        user = {
            "id": session["uid"],
            "username": session.get("username"),
            "role": session.get("role"),
        }
    g.auth_user = user
    return user


def current_uid():
    u = _user_from_request()
    return u["id"] if u else None


def is_admin() -> bool:
    u = _user_from_request()
    return bool(u and u.get("role") == "admin")


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if current_uid() is None:
            return jsonify({"error": "Not signed in."}), 401
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if current_uid() is None:
            return jsonify({"error": "Not signed in."}), 401
        if not is_admin():
            return jsonify({"error": "Admin only."}), 403
        return fn(*args, **kwargs)

    return wrapper


def _owns_facility(facility_id: int):
    """Return the facility (with secrets) if the caller may use it, else None."""
    fac = database.get_facility_secrets(facility_id)
    if fac is None:
        return None
    if fac.get("owner_id") == current_uid() or is_admin():
        return fac
    return None


# --------------------------------------------------------------------------
# UI (admin / management console)
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("server.html")


@app.route("/favicon.ico")
def favicon():
    from flask import send_from_directory

    return send_from_directory(config.STATIC_DIR, "favicon.ico")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/download/desktop-app")
@login_required
def download_desktop_app():
    """Serve the desktop launcher installer to any signed-in user (user or admin).
    The .exe lives next to the app root in ``exe4endusers/``."""
    from flask import send_from_directory

    return send_from_directory(
        config.BASE_DIR / "exe4endusers",
        "GatewayPCC-Setup.exe",
        as_attachment=True,
    )


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
def _complete_login(user: dict):
    """Finish an authenticated login: set the browser session and return the
    user plus a desktop bearer token."""
    session["uid"] = user["id"]
    session["role"] = user["role"]
    session["username"] = user["username"]
    token = auth.make_token(app.secret_key, user)
    return jsonify({**user, "token": token})


@app.post("/api/auth/login")
def api_login():
    """Verify username + password and complete the login immediately."""
    data = request.get_json(force=True)
    user = database.verify_user(data.get("username", ""), data.get("password", ""))
    if user is None:
        return jsonify({"error": "Invalid username or password."}), 401
    return _complete_login(user)


@app.post("/api/auth/logout")
def api_app_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/auth/me")
def api_me():
    u = _user_from_request()
    if u is None:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, **u})


# --------------------------------------------------------------------------
# User management (admin)
# --------------------------------------------------------------------------
@app.get("/api/users")
@admin_required
def api_list_users():
    return jsonify(database.list_users())


@app.post("/api/users")
@admin_required
def api_create_user():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400
    try:
        return jsonify(database.create_user(username, password, data.get("role", "user"))), 201
    except Exception:  # noqa: BLE001 - likely UNIQUE violation
        return jsonify({"error": "That username already exists."}), 400


@app.post("/api/users/<int:user_id>/password")
@admin_required
def api_reset_password(user_id: int):
    data = request.get_json(force=True)
    if not (data.get("password") or ""):
        return jsonify({"error": "Password is required."}), 400
    database.set_password(user_id, data["password"])
    return jsonify({"ok": True})


@app.delete("/api/users/<int:user_id>")
@admin_required
def api_delete_user(user_id: int):
    if user_id == current_uid():
        return jsonify({"error": "You cannot delete your own account."}), 400
    database.delete_user(user_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
def _scope_owner(requested_owner_id):
    """Which owner's data to act on. Admins may target any user via ?owner_id=;
    everyone else is pinned to themselves."""
    if is_admin() and requested_owner_id:
        return int(requested_owner_id)
    return current_uid()


@app.get("/api/clients")
@login_required
def api_list_clients():
    owner = _scope_owner(request.args.get("owner_id", type=int))
    return jsonify(database.list_clients(owner))


@app.post("/api/clients")
@login_required
def api_create_client():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Client name is required."}), 400
    owner = _scope_owner(data.get("owner_id"))
    return jsonify(database.create_client(owner, name)), 201


@app.delete("/api/clients/<int:client_id>")
@login_required
def api_delete_client(client_id: int):
    if database.client_owner(client_id) != current_uid() and not is_admin():
        return jsonify({"error": "Not found."}), 404
    database.delete_client(client_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Facilities
# --------------------------------------------------------------------------
@app.get("/api/facilities")
@login_required
def api_list_facilities():
    client_id = request.args.get("client_id", type=int)
    owner = _scope_owner(request.args.get("owner_id", type=int))
    return jsonify(database.list_facilities(owner, client_id))


@app.post("/api/facilities")
@login_required
def api_create_facility():
    data = request.get_json(force=True)
    client_id = data.get("client_id")
    if not client_id:
        return jsonify({"error": "client_id is required."}), 400
    owner = database.client_owner(int(client_id))
    if owner is None or (owner != current_uid() and not is_admin()):
        return jsonify({"error": "Client not found."}), 404
    if not (data.get("name") or "").strip():
        return jsonify({"error": "Facility name is required."}), 400
    new_id = database.create_facility(owner, int(client_id), data)
    return jsonify({"ok": True, "id": new_id, "client_id": int(client_id)}), 201


@app.put("/api/facilities/<int:facility_id>")
@login_required
def api_update_facility(facility_id: int):
    if _owns_facility(facility_id) is None:
        return jsonify({"error": "Not found."}), 404
    database.update_facility(facility_id, request.get_json(force=True))
    return jsonify({"ok": True})


@app.delete("/api/facilities/<int:facility_id>")
@login_required
def api_delete_facility(facility_id: int):
    if _owns_facility(facility_id) is None:
        return jsonify({"error": "Not found."}), 404
    database.delete_facility(facility_id)
    return jsonify({"ok": True})


@app.get("/api/facilities/<int:facility_id>/totp")
@login_required
def api_facility_totp(facility_id: int):
    """Current code from a facility's STORED secret — confirms it's saved/valid."""
    import time

    import pyotp

    sec = _owns_facility(facility_id)
    if not sec or not sec.get("totp_secret"):
        return jsonify({"configured": False})
    try:
        totp = pyotp.TOTP(sec["totp_secret"])
        return jsonify({
            "configured": True,
            "valid": True,
            "code": totp.now(),
            "remaining": totp.interval - int(time.time()) % totp.interval,
        })
    except Exception:  # noqa: BLE001
        return jsonify({"configured": True, "valid": False})


@app.get("/api/facilities/<int:facility_id>/launch-config")
@login_required
def api_launch_config(facility_id: int):
    """Deliver everything the DESKTOP agent needs to launch + sign in:
    the facility's decrypted credentials/TOTP and the proxy/login settings
    (including the decrypted proxy password). Sent over HTTPS, owner-scoped,
    and only to an authenticated caller — this is the sensitive endpoint."""
    fac = _owns_facility(facility_id)
    if fac is None:
        return jsonify({"error": "Not found."}), 404
    settings = dict(database.get_settings())
    settings["proxy_password"] = database.get_proxy_password()  # decrypted, for the relay
    facility = {
        "id": fac["id"],
        "client_id": fac.get("client_id"),
        "name": fac.get("name", ""),
        "location": fac.get("location", ""),
        "site_url": fac.get("site_url", ""),
        "username": fac.get("username", ""),
        "password": fac.get("password", ""),
        "totp_secret": fac.get("totp_secret", ""),
        "username_selector": fac.get("username_selector", ""),
        "password_selector": fac.get("password_selector", ""),
        "submit_selector": fac.get("submit_selector", ""),
        "totp_selector": fac.get("totp_selector", ""),
    }
    return jsonify({"facility": facility, "settings": settings})


@app.post("/api/totp-preview")
@login_required
def api_totp_preview():
    """Return the current 6-digit code for a secret, to confirm it's valid."""
    import time

    import pyotp

    data = request.get_json(force=True)
    secret = (data.get("secret") or "").replace(" ", "").strip()
    if not secret:
        return jsonify({"error": "No secret provided."}), 400
    try:
        totp = pyotp.TOTP(secret)
        code = totp.now()
    except Exception:  # noqa: BLE001 - invalid base32, etc.
        return jsonify({"error": "Not a valid authenticator secret."}), 422
    remaining = totp.interval - int(time.time()) % totp.interval
    return jsonify({"code": code, "remaining": remaining})


@app.post("/api/facilities/decode-qr")
@login_required
def api_decode_qr():
    """Decode an uploaded QR image and return the TOTP secret (not stored here)."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    secret = qrtools.extract_secret(request.files["file"].read())
    if not secret:
        return jsonify({"error": "Could not read a QR code from that image."}), 422
    return jsonify({"secret": secret})


# --------------------------------------------------------------------------
# Settings (global; admins may change them, any signed-in user may read them)
# --------------------------------------------------------------------------
def _public_settings(s: dict) -> dict:
    s = dict(s)
    s["has_proxy_password"] = "1" if s.get("proxy_password") else "0"
    s.pop("proxy_password", None)
    return s


@app.get("/api/settings")
@login_required
def api_get_settings():
    return jsonify(_public_settings(database.get_settings()))


@app.post("/api/settings")
@admin_required
def api_update_settings():
    data = request.get_json(force=True)
    return jsonify(_public_settings(database.update_settings(data)))


def main() -> None:
    database.init_db()
    url = f"http://{config.HOST}:{config.SERVER_PORT}"
    print(f"Gateway PCC SERVER (website) running at {url}")
    print(f"Admin login: {config.ADMIN_USERNAME}  (set ADMIN_USERNAME/ADMIN_PASSWORD in .env)")
    print("Open the address above in your browser. Press CTRL+C to stop.")
    try:
        app.run(host=config.HOST, port=config.SERVER_PORT, debug=False)
    except OSError as exc:
        print(f"\nERROR: could not start on {url} ({exc}).")
        print("Port may be in use. Close other instances, or set GATEWAY_PORT to a free port.")
        input("Press Enter to close...")


if __name__ == "__main__":
    main()
