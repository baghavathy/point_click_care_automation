#!/usr/bin/env bash
# Watchdog for https://pcc.arithmed.com
#
# Point Click Care Automation (Gateway PCC SERVER) is a plain uv/Flask app that
# runs IN this sparkbridge desktop container (no Docker). This one watchdog owns
# BOTH legs: it (re)starts the gunicorn app AND the named cloudflared tunnel, and
# self-heals either one every WATCH_INTERVAL. The app is pinned to a private port
# (8425) outside every other project's range, and the tunnel matches only its own
# --config, so this never touches vlabs/epiclecrm/epicleocr/medcian.
set -u
PROJ="/config/workspace/projects/Point Click Care Automation/PointClickCareAutomation/point-click-care-automation-app"
cd "$PROJ" || exit 1

# --- fixed private port. The tunnel fronts this exact port every time. ---
APP_PORT=8425
APP_HOST=0.0.0.0
APP_URL="http://127.0.0.1:${APP_PORT}"
APP_LOG="/config/pcc-app.log"

# Absolute binaries — the s6 boot hook runs with a minimal PATH that excludes
# /config/.local/bin, so a bare `uv`/`cloudflared` would fail "No such file".
UV_BIN="$(command -v uv 2>/dev/null)";        [ -x "$UV_BIN" ] || UV_BIN="/config/.local/bin/uv"
CF_BIN="$(command -v cloudflared 2>/dev/null)"; [ -x "$CF_BIN" ] || CF_BIN="/config/.local/bin/cloudflared"

CF_CONFIG="/config/.cloudflared-pcc/config.yml"
CF_LOG="/config/pcc-tunnel.log"
WATCH_INTERVAL=30

# Cross-container restart trigger — touch to ask for a clean app restart without
# a shell in this container; the watchdog honors it within ~5s and deletes it.
REQ="$PROJ/.restart-request"

app_up()      { [ "$(curl -s -o /dev/null -w '%{http_code}' "$APP_URL/healthz" 2>/dev/null)" = "200" ]; }
# Match on our exact bind address, not "wsgi:app" — that string is also a
# substring of EpicleCRM's "wsgi:application" target and would false-positive.
app_running() { pgrep -f -- "-b ${APP_HOST}:${APP_PORT} .*wsgi:app$" >/dev/null 2>&1; }
tun_up()      { pgrep -f "cloudflared --config $CF_CONFIG" >/dev/null 2>&1; }

start_app() {
  echo "[serve] starting Point Click Care Automation (Flask+gunicorn) on ${APP_HOST}:${APP_PORT} ..."
  # This container ships VIRTUAL_ENV=/lsiopy; unset it so uv uses the project .venv.
  # Background with a plain `setsid … &` — do NOT wrap in `( … & )` (that subshell
  # idiom leaks a second copy of THIS watch-loop bash, spawning a duplicate watcher
  # on every app (re)start). cwd is already $PROJ from the top-level cd.
  # Cap BLAS/OpenMP thread pools: numpy/opencv size these off the host's raw CPU
  # count (20 here), not the container's actual cgroup quota (2), so left unset
  # each worker burns dozens of idle OS threads — see qrtools.py's cv2.setNumThreads
  # comment. This container has a tight combined process+thread budget shared by
  # every project, so unbounded native thread pools here starve everyone else.
  env -u VIRTUAL_ENV \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
    setsid "$UV_BIN" run gunicorn --chdir "$PROJ" -b "${APP_HOST}:${APP_PORT}" \
      --workers 2 --threads 8 --worker-class gthread --timeout 120 \
      wsgi:app \
    >> "$APP_LOG" 2>&1 < /dev/null &
}

start_tun() {
  echo "[serve] starting cloudflared tunnel ($CF_BIN) ..."
  setsid "$CF_BIN" --config "$CF_CONFIG" tunnel run >> "$CF_LOG" 2>&1 < /dev/null &
}

# Honor a restart-request sentinel: kill the app so the next ensure() respawns it
# fresh (picks up new code). Removes the sentinel first so a failed kill can't loop.
# Kill by PORT (fuser) rather than `pkill -f <cmdline>` — a broad -f match would
# also kill any unrelated process that merely mentions the gunicorn command string.
cycle_app() {
  echo "[serve] restart requested via $REQ — cycling app on :${APP_PORT}"
  rm -f "$REQ"
  fuser -k "${APP_PORT}/tcp" 2>/dev/null
  sleep 2
}

# Is the pidfile's pid actually one of OUR watchers? Guards a stale/reused pid
# (after a recreate/reboot) from masquerading as a live watcher and blocking start.
watcher_alive() {
  local p="$1"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null \
    && grep -qa 'serve-pcc' "/proc/$p/cmdline" 2>/dev/null
}

# Best-effort re-install of the s6 boot hook (wiped on a full container recreate).
# No-op unless we can write /custom-cont-init.d (i.e. running as root).
redrop_hook() {
  local src="$PROJ/scripts/99-pcc.sh" dst="/custom-cont-init.d/99-pcc.sh"
  [ -x "$src" ] || return 0
  if [ ! -f "$dst" ] && [ -w /custom-cont-init.d ] 2>/dev/null; then
    cp "$src" "$dst" 2>/dev/null && chmod +x "$dst" 2>/dev/null && echo "[serve] re-dropped $dst"
  fi
}

ensure() {
  # App: only spawn when neither healthy NOR already coming up (avoids a second
  # gunicorn losing the bind race and dying "address already in use").
  app_up || app_running || start_app
  tun_up || start_tun
}

case "${1:-ensure}" in
  status)
    app_up  && echo "app UP"    || echo "app DOWN"
    tun_up  && echo "tunnel UP" || echo "tunnel DOWN"
    exit 0 ;;
  watch)
    PIDFILE="/config/.pcc-watch.pid"
    if [ -f "$PIDFILE" ] && watcher_alive "$(cat "$PIDFILE" 2>/dev/null)"; then
      echo "[serve] watcher already running — exiting"; exit 0; fi
    echo $$ > "$PIDFILE"
    redrop_hook
    echo "[serve] watch loop started (pid $$, every ${WATCH_INTERVAL}s)"
    while true; do
      [ -f "$REQ" ] && cycle_app
      ensure
      # Health-heal every WATCH_INTERVAL, but poll the restart sentinel every ~5s so
      # a host-triggered restart is honored promptly (not up to a full interval late).
      waited=0
      while [ "$waited" -lt "$WATCH_INTERVAL" ]; do
        sleep 5; waited=$((waited + 5))
        [ -f "$REQ" ] && break
      done
    done ;;
  *)
    ensure
    echo "[serve] done (app=$(app_up && echo y||echo n) tun=$(tun_up && echo y||echo n))" ;;
esac
