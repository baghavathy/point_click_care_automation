"""Gateway PCC — DESKTOP agent (the installed ``.exe``).

Runs entirely on the user's machine. It serves a small UI on 127.0.0.1, signs in
to the hosted cloud, lists ONLY the signed-in user's facilities, and — on launch
— pulls that facility's decrypted credentials from the cloud and drives the local
Firefox (US proxy + auto-fill of username / password / OTP) via ``automation``.

No database lives here; the cloud is the source of truth. The browser UI only
ever talks to this local server, which forwards data calls to the cloud (holding
the bearer token) and performs the Selenium work locally.
"""
from __future__ import annotations

import functools
import threading
import webbrowser

from flask import Flask, jsonify, render_template, request, send_from_directory

from . import automation, config, reportstore
from .cloud import CloudClient, CloudError

app = Flask(
    __name__,
    template_folder=str(config.TEMPLATES_DIR),
    static_folder=str(config.STATIC_DIR),
)

# One signed-in user per desktop session — a single shared cloud client is fine.
cloud = CloudClient()


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not cloud.token:
            return jsonify({"error": "Not signed in."}), 401
        return fn(*args, **kwargs)

    return wrapper


def _cloud_call(fn):
    """Wrap a handler so CloudError turns into a clean JSON error response."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CloudError as exc:
            return jsonify({"error": str(exc)}), (exc.status or 502)

    return wrapper


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("desktop.html")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(config.STATIC_DIR, "favicon.ico")


# --------------------------------------------------------------------------
# Server selection (which Gateway server to talk to — editable, persisted)
# --------------------------------------------------------------------------
@app.get("/api/server")
def api_get_server():
    return jsonify({"url": cloud.base_url})


@app.post("/api/server")
def api_set_server():
    data = request.get_json(force=True)
    cloud.set_server(data.get("url", ""))
    return jsonify({"url": cloud.base_url})


# --------------------------------------------------------------------------
# Authentication (delegated to the server)
# --------------------------------------------------------------------------
@app.post("/api/auth/login")
@_cloud_call
def api_login():
    data = request.get_json(force=True)
    # The login screen can carry the chosen server URL; apply it before signing in.
    if data.get("server_url"):
        cloud.set_server(data["server_url"])
    return jsonify(cloud.login(data.get("username", ""), data.get("password", "")))


@app.post("/api/auth/logout")
def api_logout():
    cloud.logout()
    return jsonify({"ok": True})


@app.get("/api/auth/me")
def api_me():
    user = cloud.me()
    if user is None:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, **user})


# --------------------------------------------------------------------------
# Data (forwarded to the cloud, scoped to the signed-in user there)
# --------------------------------------------------------------------------
@app.get("/api/clients")
@login_required
@_cloud_call
def api_clients():
    return jsonify(cloud.get("/api/clients"))


@app.get("/api/facilities")
@login_required
@_cloud_call
def api_facilities():
    return jsonify(cloud.get("/api/facilities",
                             {"client_id": request.args.get("client_id")}))


@app.get("/api/settings")
@login_required
@_cloud_call
def api_settings():
    return jsonify(cloud.get("/api/settings"))


@app.get("/api/facilities/<int:facility_id>/totp")
@login_required
@_cloud_call
def api_totp(facility_id: int):
    return jsonify(cloud.get(f"/api/facilities/{facility_id}/totp"))


# --------------------------------------------------------------------------
# Launch / logout / sessions — performed LOCALLY with Selenium
# --------------------------------------------------------------------------
@app.post("/api/launch/<int:facility_id>")
@login_required
@_cloud_call
def api_launch(facility_id: int):
    # Pull decrypted credentials + proxy/login settings from the cloud (HTTPS).
    cfg = cloud.get(f"/api/facilities/{facility_id}/launch-config")
    facility = cfg.get("facility") or {}
    settings = cfg.get("settings") or {}
    owner_id = cloud.user.get("id") if cloud.user else None
    result = automation.launch_facility(facility, settings, owner_id)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.post("/api/logout/<int:facility_id>")
@login_required
@_cloud_call
def api_logout_session(facility_id: int):
    # Non-secret logout settings (selectors/delays/url) come from the cloud.
    settings = cloud.get("/api/settings")
    result = automation.logout_facility(facility_id, settings)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.get("/api/sessions")
@login_required
def api_sessions():
    owner_id = cloud.user.get("id") if cloud.user else None
    return jsonify(automation.list_sessions(owner_id, cloud.is_admin))


@app.post("/api/session/<int:facility_id>/focus")
@login_required
def api_session_focus(facility_id: int):
    info = automation.session_info(facility_id)
    if not info.get("active"):
        return jsonify({"ok": False, "error": "No active session."}), 400
    return jsonify({"ok": True, **info})


@app.post("/api/facilities/<int:facility_id>/reports/administration-record")
@login_required
@_cloud_call
def api_open_administration_record(facility_id: int):
    # Self-contained: launches + signs in the facility first if there's no
    # active session yet (waiting out login rather than erroring), then
    # navigates Reports -> Clinical -> Administration Record. Independent of
    # whether the operator pressed Launch first.
    cfg = cloud.get(f"/api/facilities/{facility_id}/launch-config")
    facility = cfg.get("facility") or {}
    settings = cfg.get("settings") or {}
    owner_id = cloud.user.get("id") if cloud.user else None
    result = automation.open_reports_auto(facility, settings, owner_id)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.post("/api/facilities/<int:facility_id>/reports/administration-record/run")
@login_required
@_cloud_call
def api_run_administration_record(facility_id: int):
    params = request.get_json(force=True, silent=True) or {}
    # Non-secret logout selectors/delays — same source the Logout button uses —
    # so the report run can sign the session out itself once the PDF is saved.
    logout_settings = cloud.get("/api/settings")
    result = automation.run_administration_record_report(facility_id, params, logout_settings)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.post("/api/facilities/<int:facility_id>/census/search")
@login_required
@_cloud_call
def api_census_search(facility_id: int):
    # Self-contained, like the Reports button: launches + signs in the
    # facility first if there's no active session yet, then searches for the
    # resident number, opens their Census tab, captures its results table,
    # and signs out (same logout selectors the Logout button uses).
    data = request.get_json(force=True, silent=True) or {}
    resident_number = (data.get("resident_number") or "").strip()
    if not resident_number:
        return jsonify({"ok": False, "error": "Resident number is required."}), 400
    cfg = cloud.get(f"/api/facilities/{facility_id}/launch-config")
    facility = cfg.get("facility") or {}
    settings = cfg.get("settings") or {}
    owner_id = cloud.user.get("id") if cloud.user else None
    logout_settings = cloud.get("/api/settings")
    result = automation.open_census_auto(facility, settings, resident_number,
                                          owner_id, logout_settings)
    return jsonify(result), (200 if result.get("ok") else 400)


# --------------------------------------------------------------------------
# Results — generated report PDFs, saved locally on this machine only
# --------------------------------------------------------------------------
@app.get("/api/reports/results")
@login_required
def api_list_report_results():
    owner_id = cloud.user.get("id") if cloud.user else None
    results = reportstore.list_results(None if cloud.is_admin else owner_id)
    return jsonify(results)


@app.get("/api/reports/results/<result_id>/file")
@login_required
def api_report_result_file(result_id: str):
    entry = reportstore.get_result(result_id)
    if not entry:
        return jsonify({"error": "Not found."}), 404
    owner_id = cloud.user.get("id") if cloud.user else None
    if not cloud.is_admin and entry.get("owner_id") != owner_id:
        return jsonify({"error": "Not found."}), 404
    path = reportstore.get_file_path(result_id)
    if not path:
        return jsonify({"error": "The saved file is missing from disk."}), 404
    is_html = entry.get("kind") == "html"
    # as_attachment=False: the browser opens it inline (native PDF viewer for
    # a real PDF; a plain HTML view for the HTML fallback) instead of forcing
    # a download prompt.
    ext = "html" if is_html else "pdf"
    safe_name = f"{entry['facility_name']} - {entry['period_label']}.{ext}".replace("/", "-")
    return send_from_directory(path.parent, path.name,
                                mimetype="text/html" if is_html else "application/pdf",
                                as_attachment=False, download_name=safe_name)


def main() -> None:
    config.ensure_desktop_dirs()
    url = f"http://{config.HOST}:{config.DESKTOP_PORT}"
    print(f"Gateway PCC desktop agent running at {url}")
    print(f"Server: {cloud.base_url}  (change it on the sign-in screen)")
    print("If your browser doesn't open automatically, open the address above. Press CTRL+C to stop.")
    # Open the UI in the default browser shortly after the server is up.
    if config.HOST in ("127.0.0.1", "localhost"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        # threaded=True: the Reports automation (open_administration_record /
        # run_administration_record_report) can block for tens of seconds
        # waiting on PCC's UI. Without this, Flask's dev server is
        # single-threaded and that one request freezes EVERYTHING else in the
        # app (facility list, session polling) until it finishes.
        app.run(host=config.HOST, port=config.DESKTOP_PORT, debug=False, threaded=True)
    except OSError as exc:
        print(f"\nERROR: could not start on {url} ({exc}).")
        print("Port may be in use. Close other Gateway PCC windows, or set GATEWAY_PORT to a free port.")
        input("Press Enter to close...")


if __name__ == "__main__":
    main()
