# Gateway PCC

One application, two modes — everything lives in **`point-click-care-automation-app/`**.

| Mode | What it is | Where it runs | Start it |
|---|---|---|---|
| **SERVER** | The website: user accounts, admin console, **encrypted** facility/credential vault. No browser. | Your host (e.g. `pcc.arithmed.com`) — or locally to test. | `menu.cmd` → **2** |
| **DESKTOP** | The launcher each user installs. Signs in to a server, lists *their* facilities, runs Firefox + US proxy + auto-fills username/password/OTP. | Each user's PC | `menu.cmd` → **3** (or the built `.exe`) |

The desktop and server talk over HTTPS. The desktop's **Server URL is set on the sign-in
screen** (and remembered), so the same `.exe` works against your local test server or the
published site — no rebuilding.

Storage is **SQLite** today (single `backend/database.py`); moving to MongoDB later only
touches that one file.

```
              HTTPS (TLS)
 ┌──── SERVER (backend.server) ────┐        ┌──── DESKTOP (backend.desktop) ────┐
 │ accounts + roles (admin/user)   │        │ choose Server on sign-in screen   │
 │ encrypted PCC creds + TOTP      │ <────► │ list my facilities → Launch       │
 │ admin web console               │  token │ Selenium + Firefox + US proxy     │
 │ /facilities/<id>/launch-config ─┼────────┼─► decrypted secrets (TLS), then   │
 └─────────────────────────────────┘        │   types username / password / OTP │
                                            └────────────────────────────────────┘
```

---

## Quick start (everything on one PC, to try it)

```
cd point-click-care-automation-app
menu.cmd        ->  1) Install dependencies
                ->  2) Run the SERVER     (leave it running; http://127.0.0.1:5000)
```
In a browser open `http://127.0.0.1:5000`, sign in as `admin@gatewaypcc.com` / `Epicle@1234`,
create a client + facility (with the PCC username/password and TOTP).

Then, in a **second** window:
```
cd point-click-care-automation-app
menu.cmd        ->  3) Run the DESKTOP agent
```
A browser opens at `http://127.0.0.1:5050` (the desktop uses a different port from the
server, so they don't clash). On the sign-in screen set **Server** to
`http://127.0.0.1:5000`, sign in, pick your facility, **Launch**.

> Ports: SERVER = `5000`, DESKTOP = `5050` by default. If a browser doesn't open or a page
> looks blank, the most common cause is two things on the same port — make sure you're
> opening `:5000` for the website and `:5050` for the desktop agent.

---

## Build the desktop installer (.exe)

On **Windows**: `point-click-care-automation-app/menu.cmd` → **1** (install) → **4** (build).

This runs PyInstaller (→ `dist/GatewayPCC.exe`) and then Inno Setup to produce the
one-click **`installer/GatewayPCC-Setup.exe`** — the single file you share. Users
double-click to install and can **uninstall / reinstall** from Windows "Add or remove
programs" (per-user, no admin needed; Start-menu + optional desktop shortcut).

- Needs **Inno Setup 6**; if missing, Option 4 installs it for you (winget, else downloads
  the official installer). Only if both fail does it point you to <https://jrsoftware.org/isdl.php>.
- PyInstaller + Inno Setup are **Windows-only** — build the installer on Windows.
- After install, users set the **Server** on the sign-in screen (defaults to
  `https://pcc.arithmed.com`). To change the default, edit
  `DEFAULT_SERVER_URL` in `backend/config.py` before building.

---

## Deploy the SERVER (production)

```
cd point-click-care-automation-app
uv sync
uv run gunicorn -w 3 -b 127.0.0.1:5000 wsgi:app
```
Put **nginx** in front with a Let's Encrypt cert for `pcc.arithmed.com`
(nginx terminates TLS → proxies to `127.0.0.1:5000`). Point DNS (an `A` record) at the host.
Back up `point-click-care-automation-app/data/` — the SQLite DB **and** `secret.key` (losing the key makes
stored secrets unrecoverable).

Once the domain resolves, every desktop `.exe` (default Server = that domain) just works.

---

## Security model (server-side key)

- PCC passwords/TOTP secrets are encrypted at rest (Fernet) in the server DB.
- The server can decrypt, so it delivers secrets to the desktop at launch and admins can
  pre-provision facilities for users. Therefore **HTTPS is mandatory**, the `launch-config`
  endpoint is token-protected and owner-scoped, and account passwords are stored only as hashes.
- Per-user isolation is enforced server-side (`owner_id`) — a user's `.exe` can never request
  anyone else's data.

---

## Folder map

```
point-click-care-automation-app/
├── backend/
│   ├── server.py      SERVER Flask app (website + API)        ← run: python -m backend.server
│   ├── desktop.py     DESKTOP Flask app (local agent)         ← run: python -m backend.desktop
│   ├── database.py    SQLite + Fernet vault (server)          ← swap to MongoDB here later
│   ├── auth.py        bearer tokens (server)
│   ├── qrtools.py     QR -> TOTP secret (server, admin UI)
│   ├── automation.py  Selenium / Firefox / proxy (desktop)
│   ├── proxyrelay.py  authenticated-proxy relay (desktop)
│   ├── cloud.py       server API client + Server-URL handling (desktop)
│   ├── tokenstore.py  remembers server URL + login (desktop)
│   └── config.py      paths / ports / default server URL (both)
├── frontend/          server.html + desktop.html (+ shared css, server.js/desktop.js)
├── menu.cmd           install / run server / run desktop / build installer
├── wsgi.py            gunicorn entry (server)
├── run_desktop.py     PyInstaller entry (desktop .exe)
├── GatewayPCC.spec    PyInstaller build spec
└── installer.iss      Inno Setup one-click installer
```
