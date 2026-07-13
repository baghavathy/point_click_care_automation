#!/usr/bin/env bash
# LSIO s6 boot hook (reboot trigger) for https://pcc.arithmed.com — runs at every
# container start as root. Launches the detached watchdog that keeps the Point
# Click Care Automation app + named cloudflared tunnel up whenever the Spark is on.
#
# This stub lives on the EPHEMERAL container layer and is wiped on a full container
# recreate; the real logic + the recreate-proof XDG autostart trigger live on
# /config. Re-drop this stub after a recreate with:
#   cp "/config/workspace/projects/Point Click Care Automation/PointClickCareAutomation/point-click-care-automation-app/scripts/99-pcc.sh" /custom-cont-init.d/ && chmod +x /custom-cont-init.d/99-pcc.sh
SERVE="/config/workspace/projects/Point Click Care Automation/PointClickCareAutomation/point-click-care-automation-app/scripts/serve-pcc.sh"
[ -x "$SERVE" ] || { echo "[pcc-boot] $SERVE not executable — skipping"; exit 0; }
echo "[pcc-boot] launching watchdog"
setsid bash "$SERVE" watch >> /config/pcc-serve.log 2>&1 < /dev/null &
