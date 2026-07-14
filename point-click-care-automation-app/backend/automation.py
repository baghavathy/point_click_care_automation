"""Selenium / Firefox automation: launch the site with the US proxy enabled
and sign in to the selected facility.

FoxyProxy note
--------------
"FoxyProxy for USA" simply routes the browser through a US proxy endpoint.
We reproduce that behaviour directly on the Firefox profile via the
``network.proxy.*`` preferences (configured in Settings). This is the most
reliable way to guarantee the proxy is active *before* the login page loads,
which is exactly the requirement here.

If the proxy requires a username/password, Firefox would normally show a native
login dialog that Selenium cannot fill. To avoid that, an authenticated HTTP
proxy is routed through a local no-auth relay (see ``proxyrelay.py``) that
injects the credentials upstream, so the page loads without any prompt.

If you would rather drive the real FoxyProxy extension, set ``foxyproxy_xpi``
in Settings to the path of ``foxyproxy_standard.xpi`` — it will be installed
into the session in addition to the profile-level proxy.
"""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

import pyotp
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import config, proxyrelay, reportstore


def _log(msg: str) -> None:
    """Print to the server console and append to data/automation.log."""
    line = f"[automation] {msg}"
    print(line, flush=True)
    try:
        config.ensure_desktop_dirs()
        with open(config.DESKTOP_DATA_DIR / "automation.log", "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass

# Active browser sessions, keyed by facility_id. Each value is a dict:
#   {"driver": <Firefox>, "owner_id": int, "facility_name": str, "username": str}
_sessions: dict[int, dict] = {}
_lock = threading.Lock()


def _driver_of(facility_id: int):
    s = _sessions.get(facility_id)
    return s["driver"] if s else None

# 2-second gap between each OTP digit, per requirement.
OTP_DIGIT_GAP = 2
# If fewer than this many seconds remain in the current TOTP window, wait for a
# fresh code so it stays valid for the whole (slow) entry.
OTP_ENTRY_BUDGET = 18
# Default seconds to wait for the OTP screen before deciding the device is
# remembered (overridable via the "otp_wait_seconds" setting).
OTP_SCREEN_WAIT = 30
# Ordered CSS selectors used to spot the OTP input(s), specific -> general.
OTP_SELECTORS = [
    "input.MuiOutlinedInput-input[type='text']",
    "input.MuiInputBase-input[type='text']",
    "input[autocomplete='one-time-code']",
    "input[inputmode='numeric']",
    "input[name*='otp' i]",
    "input[name*='code' i]",
    "input[name*='token' i]",
    "input[id*='otp' i]",
    "input[id*='code' i]",
    "input[type='tel']",
    "input[type='text']",  # last resort on the 2FA screen
]


# --------------------------------------------------------------------------
# Firefox / proxy setup
# --------------------------------------------------------------------------
def _build_options(settings: dict[str, str]) -> Options:
    options = Options()
    if settings.get("headless") == "1":
        options.add_argument("-headless")

    if settings.get("proxy_enabled") == "1":
        host = (settings.get("proxy_host") or "").strip()
        port = (settings.get("proxy_port") or "").strip()
        scheme = (settings.get("proxy_scheme") or "http").strip().lower()
        if not host or not port:
            raise ValueError(
                "FoxyProxy (USA) is enabled but proxy host/port are not set in Settings."
            )
        port_int = int(port)
        username = (settings.get("proxy_username") or "").strip()

        # 1 == manual proxy configuration
        options.set_preference("network.proxy.type", 1)
        if scheme == "socks":
            # SOCKS auth is not handled by the relay; pass through directly.
            options.set_preference("network.proxy.socks", host)
            options.set_preference("network.proxy.socks_port", port_int)
            options.set_preference("network.proxy.socks_version", 5)
            options.set_preference(
                "network.proxy.socks_remote_dns",
                settings.get("proxy_socks_remote_dns", "1") == "1",
            )
        else:
            if username:
                # Authenticated HTTP proxy: route Firefox through a local no-auth
                # relay that injects the credentials upstream. This avoids the
                # Firefox proxy-login dialog entirely.
                local_port = proxyrelay.ensure_relay(
                    host, port_int, username, settings.get("proxy_password", "")
                )
                host, port_int = "127.0.0.1", local_port
            options.set_preference("network.proxy.http", host)
            options.set_preference("network.proxy.http_port", port_int)
            options.set_preference("network.proxy.ssl", host)
            options.set_preference("network.proxy.ssl_port", port_int)
        options.set_preference("network.proxy.no_proxies_on", "")
    return options


def _launch_driver(settings: dict[str, str]) -> webdriver.Firefox:
    options = _build_options(settings)
    driver = webdriver.Firefox(options=options)

    # Optionally also install the real FoxyProxy extension if provided.
    xpi = (settings.get("foxyproxy_xpi") or "").strip()
    if xpi:
        try:
            driver.install_addon(xpi, temporary=True)
        except WebDriverException:
            pass  # Profile-level proxy above still applies.
    return driver


# --------------------------------------------------------------------------
# Login helpers
# --------------------------------------------------------------------------
def _find(driver, wait, selector: str, fallbacks: list[tuple[str, str]]):
    """Find an element by an explicit CSS selector, else by fallback strategies."""
    if selector:
        return wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
    last_err: Optional[Exception] = None
    for by, value in fallbacks:
        try:
            return WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((by, value))
            )
        except TimeoutException as err:
            last_err = err
    raise last_err or TimeoutException("Element not found")


def _enter_text(el, value: str) -> None:
    """Type a value into a field so React/MUI controlled inputs register it."""
    try:
        el.click()
    except WebDriverException:
        pass
    try:
        el.clear()
    except WebDriverException:
        pass
    el.send_keys(value)


def _click_by_text(driver, labels: list[str], timeout: int = 8) -> bool:
    """Click a button/span/link whose visible text matches one of ``labels``.

    PointClickCare's "Next" control is a ``<span>Next</span>`` inside a button,
    so we match the text on any clickable-ish element (case-insensitive).
    """
    lc = "abcdefghijklmnopqrstuvwxyz"
    uc = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for label in labels:
        target = label.lower()
        xpath = (
            "//*[self::button or self::a or self::span or @role='button' "
            "or @type='submit']"
            f"[normalize-space(translate(., '{uc}', '{lc}'))='{target}']"
        )
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            el.click()
            return True
        except (TimeoutException, WebDriverException):
            continue
    return False


def _perform_login(driver, facility: dict[str, Any], settings: dict[str, str]) -> None:
    # Navigate to the actual login form in the SAME browser, if configured
    # (e.g. https://login.pointclickcare.com/home/userLogin.xhtml).
    login_url = (settings.get("login_url") or "").strip()
    if login_url:
        # Make sure the launched site is up before moving on, then navigate.
        try:
            WebDriverWait(driver, 30).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            pass
        driver.get(login_url)

    wait = WebDriverWait(driver, 40)

    # ---- Screen 1: username (#username / name="un"), then click "Next" ----
    if facility.get("username"):
        user_el = _find(
            driver, wait, facility.get("username_selector", ""),
            [
                (By.CSS_SELECTOR, "#username"),
                (By.CSS_SELECTOR, "input[name='un']"),
                (By.CSS_SELECTOR, "input[type='email']"),
                (By.CSS_SELECTOR, "input[name*='user' i]"),
                (By.CSS_SELECTOR, "input[id*='user' i]"),
                (By.CSS_SELECTOR, "input[type='text']"),
            ],
        )
        _enter_text(user_el, facility["username"])
        _log("Username entered; clicking Next.")
        if not _click_by_text(driver, ["Next", "Continue", "Sign in", "Log in", "Login"]):
            user_el.send_keys(Keys.RETURN)

    # ---- Screen 2: password (data-test="login-password-input"), then submit ----
    if facility.get("password"):
        pass_el = _find(
            driver, WebDriverWait(driver, 40),
            facility.get("password_selector", ""),
            [
                (By.CSS_SELECTOR, "input[data-test='login-password-input']"),
                (By.CSS_SELECTOR, "input[type='password']"),
            ],
        )
        _enter_text(pass_el, facility["password"])
        _log("Password entered; clicking Sign in.")
        if not _click_by_text(
            driver, ["Sign in", "Log in", "Login", "Next", "Submit", "Continue"]
        ):
            pass_el.send_keys(Keys.RETURN)

    # Handle the 2FA / TOTP screen (only when it actually appears).
    _handle_totp(driver, facility, settings)


def _set_remember_device(driver) -> None:
    """Tick the 'remember this device (14 days)' checkbox if it's present.

    MUI renders the real <input> with tabindex=-1 and a styled wrapper, so we
    click the surrounding label/span and fall back to a JS click.
    """
    try:
        cb = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#checkbox_rememberDevice")
            )
        )
    except TimeoutException:
        return
    if cb.is_selected():
        return
    for xp in ("./ancestor::label[1]", "./parent::*"):
        try:
            cb.find_element(By.XPATH, xp).click()
            if cb.is_selected():
                return
        except WebDriverException:
            continue
    try:
        driver.execute_script("arguments[0].click();", cb)
    except WebDriverException:
        pass


def _focus(driver, el) -> None:
    """Bring an element into view and give it focus (MUI inputs need this)."""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except WebDriverException:
        pass
    try:
        ActionChains(driver).move_to_element(el).click().perform()
    except WebDriverException:
        try:
            el.click()
        except WebDriverException:
            pass


def _js_set_value(driver, el, value: str) -> None:
    """Set a React-controlled input's value and fire input/change events."""
    driver.execute_script(
        """
        const el = arguments[0], val = arguments[1];
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, val);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        """,
        el, value,
    )


def _boxes_filled(boxes) -> int:
    n = 0
    for b in boxes:
        try:
            if (b.get_attribute("value") or "").strip():
                n += 1
        except WebDriverException:
            pass
    return n


def _type_otp_multibox(driver, boxes, digits) -> None:
    """Fill six (or N) auto-advancing OTP boxes, with a 2-second gap per digit.

    Primary path: focus the first box and send each digit to the *focused*
    element — the MUI component auto-advances, carrying each digit to the next
    box. Fallback: explicitly target each box (click / send / JS) if the
    auto-advance fill came up short.
    """
    _focus(driver, boxes[0])
    for ch in digits:
        try:
            ActionChains(driver).send_keys(ch).perform()
        except WebDriverException:
            pass
        time.sleep(OTP_DIGIT_GAP)

    if _boxes_filled(boxes) >= len(digits):
        return

    _log(
        f"Auto-advance fill incomplete ({_boxes_filled(boxes)}/{len(digits)}); "
        "retrying box-by-box."
    )
    for i, ch in enumerate(digits):
        if i >= len(boxes):
            break
        try:
            _focus(driver, boxes[i])
            boxes[i].clear()
            boxes[i].send_keys(ch)
            if not (boxes[i].get_attribute("value") or "").strip():
                _js_set_value(driver, boxes[i], ch)
        except WebDriverException:
            try:
                _js_set_value(driver, boxes[i], ch)
            except WebDriverException:
                pass
        time.sleep(OTP_DIGIT_GAP)


def _type_otp(driver, boxes, code: str) -> None:
    """Type the OTP — multiple boxes (one digit each) or a single combined box,
    always with a 2-second gap between digits."""
    digits = [c for c in code if c.isdigit()]
    if len(boxes) >= 2:
        _type_otp_multibox(driver, boxes, digits)
    else:
        box = boxes[0]
        _focus(driver, box)
        try:
            box.clear()
        except WebDriverException:
            pass
        for ch in digits:
            box.send_keys(ch)
            time.sleep(OTP_DIGIT_GAP)
        try:
            if (box.get_attribute("value") or "").strip() == "":
                _log("Keystrokes did not register; setting OTP via JS fallback.")
                _js_set_value(driver, box, "".join(digits))
        except WebDriverException:
            pass


def _collect_visible(driver, selector: str) -> list:
    """Visible, enabled, de-duplicated elements matching a CSS selector."""
    seen: set = set()
    out = []
    try:
        found = driver.find_elements(By.CSS_SELECTOR, selector)
    except WebDriverException:
        return out
    for e in found:
        if e.id in seen:
            continue
        seen.add(e.id)
        try:
            if e.is_displayed() and e.is_enabled():
                out.append(e)
        except WebDriverException:
            continue
    return out


def _scan_for_otp(driver, selectors: list[str], timeout: int):
    """Poll the main document AND every iframe for the OTP field(s).

    Returns the list of boxes; when found inside an iframe the driver is left
    focused on that frame so the caller can interact with it. Returns [] if
    nothing matched within ``timeout`` seconds.
    """
    end = time.time() + timeout
    while time.time() < end:
        driver.switch_to.default_content()
        for sel in selectors:
            boxes = _collect_visible(driver, sel)
            if boxes:
                return boxes
        # Search inside any iframes (PCC sometimes hosts the form in one).
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
            except WebDriverException:
                continue
            for sel in selectors:
                boxes = _collect_visible(driver, sel)
                if boxes:
                    return boxes  # stay focused in this frame
        driver.switch_to.default_content()
        time.sleep(0.5)
    return []


def _handle_totp(driver, facility: dict[str, Any], settings: dict[str, str]) -> None:
    secret = (facility.get("totp_secret") or "").replace(" ", "")
    if not secret:
        return

    # Build the selector list: a facility-specific selector first, then defaults.
    selectors = list(OTP_SELECTORS)
    custom = (facility.get("totp_selector") or "").strip()
    if custom:
        selectors.insert(0, custom)

    try:
        wait = int(settings.get("otp_wait_seconds") or OTP_SCREEN_WAIT)
    except ValueError:
        wait = OTP_SCREEN_WAIT

    _log(f"Looking for OTP screen (up to {wait}s)…")
    boxes = _scan_for_otp(driver, selectors, wait)
    if not boxes:
        # Diagnostic: record what the page actually looks like.
        try:
            driver.switch_to.default_content()
            n_inputs = len(driver.find_elements(By.CSS_SELECTOR, "input"))
            url = driver.current_url
        except WebDriverException:
            n_inputs, url = -1, "?"
        _log(
            "OTP field NOT found — bypassing 2FA "
            f"(device likely remembered). url={url} inputs_on_page={n_inputs}"
        )
        return

    _log(f"OTP screen detected: {len(boxes)} field(s). Entering code.")

    # Remember this device for 14 days, if enabled in Settings (do it before
    # typing, in case entering the last digit auto-submits the form).
    if settings.get("remember_device", "1") == "1":
        _set_remember_device(driver)

    # Make sure the code stays valid for the whole (slow) entry.
    totp = pyotp.TOTP(secret)
    remaining = totp.interval - int(time.time()) % totp.interval
    if remaining < OTP_ENTRY_BUDGET:
        _log(f"Waiting {remaining + 1}s for a fresh TOTP window before typing.")
        time.sleep(remaining + 1)
    code = totp.now()

    _type_otp(driver, boxes, code)
    _log("OTP digits entered; submitting.")

    # Submit / verify if the form did not auto-submit on the last digit.
    if not _click_by_text(
        driver, ["Verify", "Sign in", "Submit", "Continue", "Confirm", "Trust"], 5
    ):
        try:
            boxes[-1].send_keys(Keys.RETURN)
        except WebDriverException:
            pass


def _worker(facility: dict, settings: dict, owner_id) -> None:
    facility_id = facility["id"]
    try:
        driver = _launch_driver(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"[automation] failed to launch Firefox: {exc}")
        return
    sess = {
        "driver": driver,
        "owner_id": owner_id,
        "facility_name": facility.get("name", ""),
        "username": facility.get("username", ""),
        "lock": threading.Lock(),
        # True once the session has reached an authenticated (non-login) page.
        # The sessions poller only treats a return to the login page as a
        # sign-out *after* this has been seen — otherwise a freshly launched
        # browser sitting on the login screen would be pruned (and quit) mid-flow.
        "seen_app": False,
    }
    # Hold the per-session lock for the ENTIRE login so the sessions poller (and
    # logout) never issue competing commands to the same WebDriver — Selenium
    # drivers are single-threaded, and concurrent access corrupts the page.
    # Acquire BEFORE publishing the session so the poller can never observe it
    # unlocked during the brief window before login starts.
    sess["lock"].acquire()
    with _lock:
        _sessions[facility_id] = sess

    try:
        # Per-facility URL wins; otherwise fall back to the global login site.
        url = (facility.get("site_url") or "").strip() or settings.get("site_url", "")
        driver.get(url)
        _perform_login(driver, facility, settings)
    except Exception as exc:  # noqa: BLE001
        print(f"[automation] login flow error for facility {facility_id}: {exc}")
    finally:
        sess["lock"].release()
    # Browser is intentionally left open for the operator to continue working.


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def launch_facility(facility: dict, settings: dict, owner_id=None) -> dict[str, Any]:
    """Launch (in a background thread) the browser and sign in to a facility.

    ``facility`` and ``settings`` are supplied by the desktop app, which fetched
    them (with decrypted secrets) from the cloud over HTTPS — this layer never
    touches a database.
    """
    if not facility:
        return {"ok": False, "error": "Facility not found."}
    if settings.get("proxy_enabled") == "1" and not (
        settings.get("proxy_host") and settings.get("proxy_port")
    ):
        return {
            "ok": False,
            "error": "FoxyProxy (USA) is enabled but proxy host/port are missing in Settings.",
        }
    thread = threading.Thread(
        target=_worker, args=(facility, settings, owner_id), daemon=True
    )
    thread.start()
    return {
        "ok": True,
        "message": f"Launching {facility.get('name', 'facility')}…",
    }


def _is_logout_url(url: str) -> bool:
    """True only for explicit sign-out / logoff pages (a session that ended)."""
    u = (url or "").lower()
    return any(k in u for k in ("logoff", "logout", "signout", "sign-out", "loggedout"))


def _is_login_url(url: str) -> bool:
    """True for a pre-authentication login / SSO page.

    Note: the PCC login page itself (``login.pointclickcare.com/.../userLogin``)
    matches here. A session is only considered *signed out* when it returns to a
    login page **after** having reached the app — never on the way in — so a
    browser still sitting on the login screen is not mistaken for a dead one.
    """
    u = (url or "").lower()
    return any(k in u for k in ("userlogin", "/login", "signin", "sign-in", "/auth"))


def list_sessions(owner_id, admin: bool = False) -> list[dict[str, Any]]:
    """Active sessions for a user; prunes ones whose window was closed or that
    were signed out manually in PCC. Never touches a driver that is busy (e.g.
    mid-login) — that would corrupt the Selenium session."""
    out = []
    to_close = []
    for fid in list(_sessions.keys()):
        s = _sessions.get(fid)
        if not s:
            continue
        if not admin and s.get("owner_id") != owner_id:
            continue
        info = {
            "facility_id": fid,
            "facility_name": s.get("facility_name", ""),
            "username": s.get("username", ""),
        }
        lock = s.get("lock")
        # If the session is busy (login in progress / logout running), don't
        # touch the driver — just report it as active.
        if lock is not None and not lock.acquire(blocking=False):
            info["url"] = "(signing in…)"
            out.append(info)
            continue
        try:
            driver = s["driver"]
            _activate_pcc_window(driver)
            url = driver.current_url
            if _is_logout_url(url):
                # An explicit sign-out/logoff page: the session has ended.
                to_close.append(fid)
                continue
            if _is_login_url(url):
                # On a login page. Only a session that had already reached the
                # app and has now bounced back to login counts as signed out;
                # a session still working through login stays listed as active.
                if s.get("seen_app"):
                    to_close.append(fid)
                    continue
                info["url"] = "(signing in…)"
                out.append(info)
                continue
            # A normal authenticated page — remember we got this far.
            s["seen_app"] = True
            info["url"] = url
            out.append(info)
        except WebDriverException:
            to_close.append(fid)
            continue
        finally:
            if lock is not None:
                lock.release()
    for fid in to_close:
        _close_session(fid)
    return out


def _close_session(facility_id: int) -> None:
    """Quit the browser (if any) and drop the session entry."""
    s = _sessions.pop(facility_id, None)
    if not s:
        return
    try:
        s["driver"].quit()
    except Exception:  # noqa: BLE001
        pass


def _activate_pcc_window(driver) -> None:
    """Point the driver at the live PCC window (handles new tabs/windows)."""
    try:
        handles = driver.window_handles
    except WebDriverException:
        return
    for h in handles:
        try:
            driver.switch_to.window(h)
            if "pointclickcare" in (driver.current_url or "").lower():
                return
        except WebDriverException:
            continue
    if handles:
        try:
            driver.switch_to.window(handles[-1])  # fall back to the newest window
        except WebDriverException:
            pass


def _dfs_frames(driver, finder, max_depth: int = 4, depth: int = 0):
    """Depth-first search through the page and all (nested) frames.

    ``finder`` is called in each frame context and should return an element (or
    truthy value) when it finds what it wants, else None. When it returns a hit,
    the driver is LEFT focused in that frame so the caller can interact.
    """
    try:
        hit = finder()
    except WebDriverException:
        hit = None
    if hit is not None:
        return hit
    if depth >= max_depth:
        return None
    try:
        count = len(driver.find_elements(By.CSS_SELECTOR, "iframe, frame"))
    except WebDriverException:
        return None
    for i in range(count):
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        if i >= len(frames):
            break
        try:
            driver.switch_to.frame(frames[i])
        except WebDriverException:
            continue
        hit = _dfs_frames(driver, finder, max_depth, depth + 1)
        if hit is not None:
            return hit
        try:
            driver.switch_to.parent_frame()
        except WebDriverException:
            driver.switch_to.default_content()
    return None


def _visible_by_css(driver, css: str):
    for e in driver.find_elements(By.CSS_SELECTOR, css):
        try:
            if e.is_displayed():
                return e
        except WebDriverException:
            continue
    return None


def _visible_by_text(driver, labels: list[str]):
    lc = "abcdefghijklmnopqrstuvwxyz"
    uc = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for label in labels:
        target = label.lower()
        xpath = (
            "//*[self::button or self::a or self::span or @role='button' "
            "or @role='menuitem' or @type='submit']"
            f"[normalize-space(translate(., '{uc}', '{lc}'))='{target}']"
        )
        for e in driver.find_elements(By.XPATH, xpath):
            try:
                if e.is_displayed():
                    return e
            except WebDriverException:
                continue
    return None


def _js_mouse_click(driver, el) -> bool:
    """Fire a full synthetic mouse sequence in the page (no OS focus needed).

    Dispatching pointer/mouse events drives the page's own handlers — this opens
    hover/click menus and activates links even when the Firefox window is behind
    the Edge window that hosts Gateway PCC.
    """
    try:
        driver.execute_script(
            """
            const el = arguments[0];
            el.scrollIntoView({block:'center'});
            const o = {bubbles:true, cancelable:true, view:window};
            try { el.focus(); } catch (e) {}
            ['pointerover','pointerenter','mouseover','pointerdown','mousedown',
             'pointerup','mouseup','click'].forEach((t) => {
              const E = t.indexOf('pointer') === 0 ? PointerEvent : MouseEvent;
              el.dispatchEvent(new E(t, o));
            });
            """,
            el,
        )
        return True
    except WebDriverException:
        return False


def _robust_click(driver, el) -> bool:
    """Click without depending on OS window focus: synthetic DOM click first,
    then a full JS mouse-event sequence, then a real pointer as a last resort."""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except WebDriverException:
        pass
    try:
        el.click()  # synthetic DOM click — works unfocused
        return True
    except WebDriverException:
        pass
    if _js_mouse_click(driver, el):
        return True
    try:
        ActionChains(driver).move_to_element(el).pause(0.1).click().perform()
        return True
    except WebDriverException:
        return False


_LOGOUT_LABELS = ["Sign Out", "Sign out", "Log out", "Logout", "Log Out", "Logoff", "Log off"]

# Shared JS snippet: a generator that walks the DOM AND every shadow root.
_JS_WALK = """
function* deepNodes(root){
  const nodes = root.querySelectorAll('*');
  for (const el of nodes){
    yield el;
    if (el.shadowRoot) yield* deepNodes(el.shadowRoot);
  }
}
function fire(el){
  el.scrollIntoView({block:'center'});
  const o = {bubbles:true, cancelable:true, view:window};
  try { el.focus(); } catch(e) {}
  ['pointerover','mouseover','pointerdown','mousedown','pointerup','mouseup','click']
    .forEach(t => el.dispatchEvent(new (t[0]==='p'?PointerEvent:MouseEvent)(t, o)));
}
function clickable(el){
  const tag = el.tagName.toLowerCase();
  return tag==='a'||tag==='button'||tag==='span'||
         el.getAttribute('role')==='button'||el.getAttribute('role')==='menuitem';
}
"""


def _js_deep_click_css(driver, selector: str) -> bool:
    """Click the first element matching a CSS selector, piercing shadow DOM."""
    try:
        return bool(driver.execute_script(
            _JS_WALK + """
            const sel = arguments[0];
            for (const el of deepNodes(document)){
              if (el.matches && el.matches(sel)) { fire(el); return true; }
            }
            return false;
            """,
            selector,
        ))
    except WebDriverException:
        return False


def _js_deep_click_text(driver, labels: list[str]) -> bool:
    """Click the first clickable element whose text matches, piercing shadow DOM."""
    try:
        return bool(driver.execute_script(
            _JS_WALK + """
            const labels = arguments[0].map(s => s.toLowerCase());
            for (const el of deepNodes(document)){
              if (!clickable(el)) continue;
              const txt = (el.textContent||'').trim().toLowerCase();
              if (labels.includes(txt)) { fire(el); return true; }
            }
            return false;
            """,
            labels,
        ))
    except WebDriverException:
        return False


def _js_deep_collect(driver) -> list:
    """Collect logout-ish controls (text/class/href), piercing shadow DOM."""
    try:
        return driver.execute_script(
            _JS_WALK + """
            const out = [];
            const keys = ['log out','logout','sign out','signout','evergreen','logoff','log off'];
            for (const el of deepNodes(document)){
              if (!clickable(el)) continue;
              const txt = (el.textContent||'').trim();
              const cls = el.getAttribute('class')||'';
              const href = el.getAttribute('href')||'';
              const blob = (txt+' '+cls+' '+href).toLowerCase();
              if (keys.some(k => blob.includes(k)))
                out.push({tag: el.tagName.toLowerCase(), text: txt.slice(0,40),
                          cls: cls.slice(0,60), href: href.slice(0,60)});
              if (out.length >= 30) break;
            }
            return out;
            """
        ) or []
    except WebDriverException:
        return []


def _try_logout_in_current_window(driver, menu_sel, logout_sel, step_delay) -> bool:
    """Open the user menu and click Sign Out — searching each frame, and within
    each frame piercing Shadow DOM via JS (PCC's evergreen-emar is web-component
    based). Works even when the Firefox window isn't OS-focused.

    ``step_delay`` adds a deliberate pause on each step so the flow is slow and
    smooth (and easy to watch).
    """
    try:
        driver.execute_script("window.focus();")
    except WebDriverException:
        pass

    def _in_frame_logout():
        # Open the user menu (shadow-piercing).
        if menu_sel and _js_deep_click_css(driver, menu_sel):
            _log(f"Logout: opened user menu via '{menu_sel}' (waiting {step_delay}s).")
            time.sleep(step_delay)
        # Click Sign Out: configured selector, then by visible text.
        if logout_sel and _js_deep_click_css(driver, logout_sel):
            time.sleep(step_delay)
            return True
        if _js_deep_click_text(driver, _LOGOUT_LABELS):
            time.sleep(step_delay)
            return True
        return None  # keep traversing frames

    time.sleep(step_delay)  # let the page settle before acting
    driver.switch_to.default_content()
    hit = _dfs_frames(driver, _in_frame_logout)
    if hit:
        _log("Logout: clicked Sign Out.")
        return True
    return False


def logout_facility(facility_id: int, settings: dict) -> dict[str, Any]:
    """Log out inside the live Firefox session — tries every window & frame.

    The Gateway PCC desktop UI calls this; it drives the Selenium-controlled
    Firefox window where the PCC session lives. ``settings`` (logout selectors,
    delays, optional logout URL) is supplied by the desktop app, fetched from
    the cloud — these are non-secret display/automation settings.
    """
    sess = _sessions.get(facility_id)
    if sess is None:
        return {"ok": False, "error": "No active session — launch the facility first."}
    driver = sess["driver"]
    lock = sess.get("lock")

    settings = settings or {}
    menu_sel = (settings.get("logout_menu_selector") or "").strip()
    logout_sel = (settings.get("logout_selector") or "").strip()
    logout_url = (settings.get("logout_url") or "").strip()
    try:
        step_delay = float(settings.get("logout_step_delay") or 4)
    except ValueError:
        step_delay = 4.0

    # Wait for any in-progress login to finish, then own the driver exclusively.
    if lock is not None:
        lock.acquire()
    try:
        # Strategy 0: an explicit logout URL (only if YOU set one in Settings).
        if logout_url:
            _activate_pcc_window(driver)
            driver.get(logout_url)
            _log(f"Logout: navigated to logout URL {logout_url}")
            time.sleep(2)
            _close_session(facility_id)  # remove the entry from Gateway PCC
            return {"ok": True, "message": "Signed out via logout URL."}

        handles = driver.window_handles
        _log(f"Logout: {len(handles)} window(s) open.")
        for h in handles:
            try:
                driver.switch_to.window(h)
                driver.switch_to.default_content()
                _log(f"Logout: trying window url={driver.current_url}")
            except WebDriverException:
                continue
            if _try_logout_in_current_window(driver, menu_sel, logout_sel, step_delay):
                _log("Logout performed.")
                time.sleep(2)
                _close_session(facility_id)  # remove the entry from Gateway PCC
                return {"ok": True, "message": "Signed out of the current session."}

        # Collect logout-ish elements (shadow-aware) so we can pin the target.
        diagnostics = []
        for h in handles:
            try:
                driver.switch_to.window(h)
                driver.switch_to.default_content()
                url = driver.current_url
            except WebDriverException:
                continue

            def _collect():
                for c in _js_deep_collect(driver):
                    c["url"] = url[:60]
                    diagnostics.append(c)
                return None  # visit every frame

            _dfs_frames(driver, _collect)

        _log(f"Logout: not found. {len(diagnostics)} candidate element(s):")
        for c in diagnostics:
            _log(f"  <{c.get('tag')}> text='{c.get('text')}' class='{c.get('cls')}' "
                 f"href='{c.get('href')}'")

        return {
            "ok": False,
            "error": "Couldn't find Sign Out automatically.",
            "diagnostics": diagnostics,
        }
    except WebDriverException as exc:
        _close_session(facility_id)
        return {"ok": False, "error": f"Session is no longer available ({exc.__class__.__name__})."}


def session_info(facility_id: int) -> dict[str, Any]:
    """Active state + current URL of the live session; re-focuses its window."""
    driver = _driver_of(facility_id)
    if driver is None:
        return {"active": False}
    try:
        _activate_pcc_window(driver)
        return {"active": True, "url": driver.current_url}
    except WebDriverException:
        _close_session(facility_id)
        return {"active": False}


# --------------------------------------------------------------------------
# Reports: navigate an already-signed-in session to a report's setup screen
# --------------------------------------------------------------------------
# PCC's Enterprise Reporting entry point — a plain link, stable across facilities.
# Primary: the nav item's own id (#QTF_reportingTab). Fallback: its href, in case
# the id ever changes. CSS_SELECTOR supports comma-separated alternatives natively.
REPORTS_LINK_CSS = "#QTF_reportingTab > a, a[href*='enterprisereporting/listing.xhtml']"

# The reporting catalog's tab bar (Recent / All / Enhanced / Admin / Clinical) is a
# widget with a stable data-tab index per tab — Clinical is data-tab="2".
CLINICAL_TAB_CSS = "a.mdl-tabs__tab[data-tab='2']"

# The "Administration Record" report under the Clinical tab's eMAR section —
# a plain report-listing link keyed by its catalog reportId (2177).
ADMIN_RECORD_LINK_CSS = "a.reportList-title[href*='reportId=2177']"


def _wait_page_ready(driver, timeout: int = 40) -> None:
    """Best-effort smart wait: the page (and any jQuery ajax it kicked off) has
    actually finished loading, before we go hunting for the next thing to click.

    document.readyState alone is not enough for PCC — many of its screens finish
    the *document* load long before their ajax-driven content (tabs, report
    lists) has rendered, so we also wait out jQuery's in-flight request count
    when jQuery is present. A short settle delay follows for post-ready
    rendering (MDL tab activation, evergreen web-component upgrades).
    """
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script(
                "return document.readyState === 'complete' "
                "&& (typeof jQuery === 'undefined' || jQuery.active === 0)"
            )
        )
    except (TimeoutException, WebDriverException):
        pass
    time.sleep(0.5)


def _find_and_click(driver, css: str, labels: list[str], timeout: int = 25) -> bool:
    """Find & click a control by CSS selector (if given) or visible text label,
    searching every frame and piercing shadow DOM, retrying until ``timeout``.

    PCC mixes classic JSF screens (content in iframes) with newer evergreen
    screens (web components / shadow DOM), and the target may not have
    rendered yet even after ``_wait_page_ready`` — so this doubles as the
    "wait for the specific element" step, not just a one-shot lookup.
    """
    end = time.time() + timeout
    while time.time() < end:
        driver.switch_to.default_content()

        def _try_here():
            if css:
                el = _visible_by_css(driver, css)
                if el:
                    return el
            if labels:
                el = _visible_by_text(driver, labels)
                if el:
                    return el
            return None

        el = _dfs_frames(driver, _try_here)
        if el is not None and _robust_click(driver, el):
            return True

        driver.switch_to.default_content()

        def _try_js_here():
            if css and _js_deep_click_css(driver, css):
                return True
            if labels and _js_deep_click_text(driver, labels):
                return True
            return None

        if _dfs_frames(driver, _try_js_here):
            return True
        time.sleep(0.4)
    return False


def _navigate_to_administration_record(driver, facility_id: int) -> Optional[str]:
    """Click Reports -> Clinical -> Administration Record. Returns an error
    message on failure, or None on success. Caller must already hold the
    session lock and have called ``_activate_pcc_window``."""
    _wait_page_ready(driver)

    _log(f"Reports[{facility_id}]: opening Reports.")
    if not _find_and_click(driver, REPORTS_LINK_CSS, ["Reports"]):
        return "Couldn't find the Reports link."
    _activate_pcc_window(driver)  # in case that click opened a new tab/window
    _wait_page_ready(driver)

    _log(f"Reports[{facility_id}]: opening Clinical tab.")
    if not _find_and_click(driver, CLINICAL_TAB_CSS, ["Clinical"]):
        return "Couldn't find the Clinical tab on the Reports page."
    _activate_pcc_window(driver)
    _wait_page_ready(driver)

    _log(f"Reports[{facility_id}]: opening Administration Record.")
    if not _find_and_click(driver, ADMIN_RECORD_LINK_CSS, ["Administration Record"]):
        return "Couldn't find Administration Record under eMAR on the Clinical tab."
    _activate_pcc_window(driver)
    _wait_page_ready(driver)
    return None


def _on_administration_record_page(driver) -> bool:
    """True if the live page is already the Administration Record report form."""
    try:
        return bool(driver.execute_script(
            "var f = document.forms['frmData'];"
            "return !!(f && f.REPORT_NAME "
            "&& f.REPORT_NAME.value === 'Administration Record Report');"
        ))
    except WebDriverException:
        return False


# JS that reads the REAL "frmData" Administration Record form as rendered by PCC
# for this specific facility — units, floors, report types, the report-template
# checklist, and the sort-by dropdowns — so our mirrored screen always matches
# what a person would actually see (unit/floor lists and the template catalog
# differ per facility), instead of a fixed, facility-specific guess.
_SCRAPE_ADMIN_RECORD_FORM_JS = r"""
const f = document.forms['frmData'];
if (!f) return {ok: false, error: "Report form not found on the current page."};

function selectOptions(name) {
  const el = f.elements[name];
  if (!el || !el.options) return [];
  return Array.from(el.options).map(o => ({
    value: o.value,
    label: (o.textContent || '').trim(),
    selected: !!o.selected,
  }));
}

function checkboxOptions(name) {
  const nodes = f.elements[name];
  if (!nodes) return [];
  const list = nodes.length !== undefined ? Array.from(nodes) : [nodes];
  return list.map(el => {
    let label = '';
    const lbl = el.closest ? el.closest('label') : null;
    if (lbl) label = lbl.textContent;
    else if (el.parentElement) label = el.parentElement.textContent;
    return { value: el.value, label: (label || el.value).trim(), checked: !!el.checked };
  });
}

return {
  ok: true,
  units: selectOptions('ESOLunitid'),
  floors: selectOptions('ESOLfloorid'),
  report_types: selectOptions('ESOLreporttype'),
  templates: checkboxOptions('ESOLreportTemplate'),
  sort_residents_by: selectOptions('sortResidentsBy'),
  sort_orders_by: selectOptions('sortOrdersBy'),
};
"""


def _scrape_administration_record_form(driver, facility_id: int) -> dict[str, Any]:
    """Best-effort read of the live form's real options. Returns {} (never
    raises) if the page shape isn't what we expect — the frontend falls back
    to its own static defaults in that case."""
    try:
        result = driver.execute_script(_SCRAPE_ADMIN_RECORD_FORM_JS)
    except WebDriverException:
        return {}
    if not result or not result.get("ok"):
        _log(f"Reports[{facility_id}]: couldn't read live form options"
             f" ({(result or {}).get('error', 'unknown')}); frontend will use defaults.")
        return {}
    return result


def open_reports_auto(facility: dict, settings: dict, owner_id=None) -> dict[str, Any]:
    """Self-contained entry point for the sidebar's Reports button: launches
    and signs in the facility if it isn't already an active session, waits
    for that sign-in to actually finish, then opens Administration Record.

    Independent of the sidebar's Launch button — an operator can press
    Reports straight away without ever pressing Launch first. If a session is
    already open (e.g. Launch WAS pressed earlier), this reuses it instead of
    starting a second browser.

    Previously, pressing Reports on a facility with no session (or one still
    mid-login) failed immediately with "session busy" — the login thread
    holds the session lock for its whole duration (OTP entry alone can take
    15-20+ seconds), so a request arriving during that window bounced off a
    short 10s wait. That's exactly what "the first press does nothing, the
    second one works" was: the first press hit the busy-session error while
    login was still running; by the second press login had finished. Waiting
    out the lock here (instead of erroring) fixes that properly.
    """
    facility_id = facility.get("id")
    sess = _sessions.get(facility_id)
    if sess is None:
        launch_result = launch_facility(facility, settings, owner_id)
        if not launch_result.get("ok"):
            return launch_result
        # The worker thread publishes the session almost immediately after the
        # browser starts (before login begins) — wait for that to happen.
        for _ in range(300):  # up to ~30s for Firefox/profile/proxy startup
            sess = _sessions.get(facility_id)
            if sess is not None:
                break
            time.sleep(0.1)
        if sess is None:
            return {"ok": False, "error": "Couldn't start the browser session."}

    # The worker holds this lock for the whole login (page load + credentials
    # + OTP entry). Wait it out rather than erroring — this is the actual fix
    # for the "press Reports twice" symptom described above.
    lock = sess.get("lock")
    if lock is not None:
        if not lock.acquire(timeout=150):
            return {"ok": False, "error": "Still signing in — try Reports again in a moment."}
        lock.release()  # open_administration_record re-acquires it itself below

    return open_administration_record(facility_id)


def open_administration_record(facility_id: int) -> dict[str, Any]:
    """Drive an already-open, already-signed-in PCC session to
    Reports -> Clinical -> Administration Record (under eMAR), landing on that
    report's parameter/setup screen, and read back the real form options so our
    mirrored screen matches exactly what PCC renders for this facility.

    Each step smart-waits for the page to actually finish loading before
    hunting for the next control (see ``_wait_page_ready`` / ``_find_and_click``)
    instead of a fixed sleep, since PCC's screens render at very different
    speeds depending on facility size and report catalog.
    """
    sess = _sessions.get(facility_id)
    if sess is None:
        return {"ok": False, "error": "No active session — launch the facility first."}
    driver = sess["driver"]
    lock = sess.get("lock")

    if lock is not None and not lock.acquire(timeout=10):
        return {"ok": False, "error": "Session is busy — try again in a moment."}
    try:
        _activate_pcc_window(driver)
        err = _navigate_to_administration_record(driver, facility_id)
        if err:
            return {"ok": False, "error": err}

        try:
            driver.execute_script("window.focus();")
        except WebDriverException:
            pass

        options = _scrape_administration_record_form(driver, facility_id)
        _log(f"Reports[{facility_id}]: Administration Record report setup opened.")
        return {"ok": True, "message": "Opened Administration Record report setup.",
                "options": options}
    except WebDriverException as exc:
        return {"ok": False, "error": f"Session is no longer available ({exc.__class__.__name__})."}
    finally:
        if lock is not None:
            lock.release()


# JS that fills every field of the real "frmData" Administration Record form from a
# plain params object, firing input/change events so PCC's own onchange handlers
# (e.g. onRecordTypeChange) run exactly as if a person had used the controls.
_APPLY_ADMIN_RECORD_PARAMS_JS = r"""
const p = arguments[0] || {};
const f = document.forms['frmData'];
if (!f) return {ok: false, error: "Report form not found on the current page."};

function fire(el) {
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
}
function setVal(name, value) {
  if (value === undefined || value === null) return;
  const el = f.elements[name];
  if (!el) return;
  el.value = value;
  fire(el);
}
function setChecked(name, values) {
  const nodes = f.elements[name];
  if (!nodes) return;
  const list = nodes.length !== undefined ? Array.from(nodes) : [nodes];
  const wanted = new Set((values || []).map(String));
  for (const el of list) {
    el.checked = wanted.has(String(el.value));
    fire(el);
  }
}
function setRadio(name, value) {
  if (value === undefined || value === null) return;
  const nodes = f.elements[name];
  if (!nodes) return;
  const list = nodes.length !== undefined ? Array.from(nodes) : [nodes];
  for (const el of list) {
    el.checked = (String(el.value) === String(value));
    if (el.checked) fire(el);
  }
}

setVal('client_id_number', p.client_id_number);
setVal('client_name', p.client_name);
setVal('ESOLunitid', p.unit_id);
setVal('ESOLfloorid', p.floor_id);
setVal('ESOLreporttype', p.report_type);
if (typeof onRecordTypeChange === 'function') {
  try { onRecordTypeChange(); } catch (e) { /* best-effort */ }
}
if (p.templates !== undefined) setChecked('ESOLreportTemplate', p.templates);
setVal('ESOLmonthSelect', p.month);
setVal('ESOLyearSelect', p.year);
if (p.weekly_start) {
  setVal('weekly_start', p.weekly_start);
  setVal('weekly_start_dummy', p.weekly_start);
}
setRadio('ESOLsortOrder', p.sort_order);
if (p.order_start_date) {
  setVal('orderStartDate', p.order_start_date);
  setVal('orderStartDate_dummy', p.order_start_date);
}
setVal('sortResidentsBy', p.sort_residents_by);
setVal('sortOrdersBy', p.sort_orders_by);
if (p.nurse_admin_notes !== undefined) {
  const el = f.elements['nurseAdminNotesCheckbox'];
  if (el) { el.checked = !!p.nurse_admin_notes; fire(el); }
}
return {ok: true};
"""


# Clicking Run Report makes PCC pop TWO windows: a resident-find.jsp loader
# while the report renders server-side, then the actual rendered PDF at
# getadminrecordreport.xhtml?fileId=... (sometimes reusing the same window,
# sometimes a further new one) — we only care about spotting the PDF one.
ADMIN_RECORD_PDF_URL_MARKER = "getadminrecordreport.xhtml"

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def _report_period_label(params: dict) -> str:
    """Human label for the Results list, e.g. "April 2026" or "Week of 04/01/2026"."""
    params = params or {}
    weekly_start = params.get("weekly_start")
    if weekly_start:
        return f"Week of {weekly_start}"
    month, year = params.get("month"), params.get("year")
    if month and year:
        try:
            return f"{MONTH_NAMES[int(month)]} {year}"
        except (ValueError, IndexError):
            pass
    return time.strftime("%Y-%m-%d")


def _wait_for_report_pdf_url(driver, baseline_handles: set, facility_id: int,
                              timeout: int = 180) -> Optional[str]:
    """Poll every open window until one of them is sitting on the rendered
    PDF. Returns that window's URL (driver stays focused on it) or None on
    timeout. PCC's report render can take a while for a large facility/date
    range, hence the generous timeout.

    Checks EVERY open window, not just ones opened after Run Report — PCC
    doesn't reliably always pop a brand new window for the PDF; sometimes it
    navigates the resident-find.jsp loader window (or even the original PCC
    window) straight to it instead. ``baseline_handles`` is only used later,
    by ``_close_extra_windows``, to know what's safe to close."""
    end = time.time() + timeout
    seen_urls: set = set()
    while time.time() < end:
        try:
            handles = driver.window_handles
        except WebDriverException:
            return None
        for h in handles:
            try:
                driver.switch_to.window(h)
                url = driver.current_url
            except WebDriverException:
                continue
            if url not in seen_urls:
                seen_urls.add(url)
                _log(f"Reports[{facility_id}]: window {h[:8]} -> {url}")
            if ADMIN_RECORD_PDF_URL_MARKER in url:
                return url
        time.sleep(1)
    _log(f"Reports[{facility_id}]: timed out waiting for the PDF; windows seen: {sorted(seen_urls)}")
    return None


def _close_extra_windows(driver, baseline_handles: set) -> None:
    """Close every window opened after ``baseline_handles`` was captured (the
    resident-find.jsp loader and the PDF window) and refocus the main PCC tab."""
    try:
        handles = driver.window_handles
    except WebDriverException:
        return
    for h in handles:
        if h in baseline_handles:
            continue
        try:
            driver.switch_to.window(h)
            driver.close()
        except WebDriverException:
            pass
    _activate_pcc_window(driver)


def _download_pdf_with_driver_cookies(driver, url: str) -> bytes:
    """Fetch the PDF's real bytes over plain HTTP(S), replaying the live
    session's cookies — this is what actually gives us a searchable PDF (real
    text layer, as PCC generated it) rather than a screenshot/print of the
    browser's built-in PDF viewer. ``driver`` must already be focused on a
    window whose origin matches ``url`` so the cookies are the right ones.

    Raises ValueError if the response isn't actually a PDF (e.g. the session
    expired mid-download and we got an HTML login page back instead) — better
    to fail loudly here than silently save garbage into Results.
    """
    cookies = driver.get_cookies()
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    origin = "/".join(url.split("/", 3)[:3])  # scheme://host
    req = urllib.request.Request(url, headers={
        "Cookie": cookie_header,
        "User-Agent": "GatewayPCC-Desktop/1.0",
        "Accept": "application/pdf,*/*",
        "Referer": origin + "/",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    if not data.startswith(b"%PDF"):
        raise ValueError(f"response wasn't a PDF (first bytes: {data[:40]!r})")
    return data


def run_administration_record_report(facility_id: int, params: dict,
                                      logout_settings: Optional[dict] = None) -> dict[str, Any]:
    """Fill the real Administration Record report form (on the already-open PCC
    session) with ``params``, click the real Run Report button, then capture the
    result end-to-end exactly like an operator would:

      1. wait for the rendered PDF (PCC pops a resident-find.jsp loader window,
         then the PDF at getadminrecordreport.xhtml),
      2. download the PDF's real bytes (searchable — a genuine text layer, not
         a screenshot) using the live session's cookies,
      3. close both popped windows,
      4. save the PDF + its metadata to the local Results store, and
      5. sign out of PCC (via ``logout_settings``, the same selectors the
         Logout button uses) so the session doesn't sit open unattended.

    Re-navigates to the report setup page first if the session isn't already
    sitting on it (e.g. time passed, or the operator clicked elsewhere). Auto-
    logout (step 5) only runs after a successful download — on any failure the
    session is left open so the operator can see what happened.
    """
    sess = _sessions.get(facility_id)
    if sess is None:
        return {"ok": False, "error": "No active session — launch the facility first."}
    driver = sess["driver"]
    lock = sess.get("lock")

    if lock is not None and not lock.acquire(timeout=10):
        return {"ok": False, "error": "Session is busy — try again in a moment."}
    entry = None
    try:
        _activate_pcc_window(driver)
        if not _on_administration_record_page(driver):
            err = _navigate_to_administration_record(driver, facility_id)
            if err:
                return {"ok": False, "error": err}
        else:
            _wait_page_ready(driver)

        try:
            result = driver.execute_script(_APPLY_ADMIN_RECORD_PARAMS_JS, params or {})
        except WebDriverException as exc:
            return {"ok": False, "error": f"Couldn't fill the report form ({exc.__class__.__name__})."}
        if not result or not result.get("ok"):
            return {"ok": False, "error": (result or {}).get("error") or "Couldn't fill the report form."}

        baseline_handles = set(driver.window_handles)
        _log(f"Reports[{facility_id}]: running Administration Record report, params={params}")
        if not _find_and_click(driver, "#runButton", ["Run Report"]):
            return {"ok": False, "error": "Couldn't find the Run Report button."}

        pdf_url = _wait_for_report_pdf_url(driver, baseline_handles, facility_id)
        if not pdf_url:
            _close_extra_windows(driver, baseline_handles)
            return {"ok": False, "error": "Timed out waiting for the report to finish generating."}

        _log(f"Reports[{facility_id}]: report ready at {pdf_url}")
        try:
            pdf_bytes = _download_pdf_with_driver_cookies(driver, pdf_url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            _log(f"Reports[{facility_id}]: PDF download failed: {exc}")
            _close_extra_windows(driver, baseline_handles)
            return {"ok": False, "error": f"Couldn't download the generated PDF ({exc})."}

        _close_extra_windows(driver, baseline_handles)

        entry = reportstore.add_result(
            owner_id=sess.get("owner_id"),
            facility_id=facility_id,
            facility_name=sess.get("facility_name", ""),
            report_name="Administration Record",
            period_label=_report_period_label(params),
            generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            pdf_bytes=pdf_bytes,
        )
        _log(f"Reports[{facility_id}]: saved report {entry['id']} ({entry['size_bytes']} bytes).")
    except WebDriverException as exc:
        return {"ok": False, "error": f"Session is no longer available ({exc.__class__.__name__})."}
    finally:
        if lock is not None:
            lock.release()

    # Lock released above — logout_facility manages the session's lock itself,
    # and a plain threading.Lock isn't reentrant, so this must happen after.
    if logout_settings is not None:
        logout_result = logout_facility(facility_id, logout_settings)
        if not logout_result.get("ok"):
            _log(f"Reports[{facility_id}]: auto-logout after report failed: "
                 f"{logout_result.get('error')}")

    return {"ok": True, "message": "Report generated, saved to Results, and signed out.",
            "result": entry}
