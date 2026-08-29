#!/bin/bash
# The whole of "Job App.app". Double-clicked from Finder it must either put the dashboard
# on screen or say exactly why it could not. There is no Terminal window to read, so every
# failure ends in a dialog, and the log lives outside the project because the project
# folder is the very thing macOS may be refusing to let us touch.

LOG="$HOME/Library/Logs/JobApp.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null
say() { echo "$(date '+%H:%M:%S') $*" >>"$LOG"; }
echo "--- launch $(date '+%Y-%m-%d %H:%M:%S') ---" >>"$LOG"

SELF="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SELF/../../.." 2>/dev/null && pwd)"
[ -f "$ROOT/main.py" ] || ROOT="__PROJECT_ROOT__"      # baked in, so a copied .app still works
say "root: $ROOT"

# Finder hands an app a bare PATH, so name the interpreter rather than hoping for one.
PY=""; PY_BARE=""
for candidate in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
  [ -x "$candidate" ] || continue
  "$candidate" -c "import sys" >/dev/null 2>&1 || continue
  [ -n "$PY_BARE" ] || PY_BARE="$candidate"
  if "$candidate" -c "import fastapi, uvicorn, docx, sqlalchemy" >/dev/null 2>&1; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || PY="$PY_BARE"
if [ -z "$PY" ]; then
  osascript -e 'display alert "Job App" message "Python 3 was not found on this Mac."' >/dev/null 2>&1
  exit 1
fi
say "interpreter: $PY"

alert() {  # title, message
  say "ALERT: $2"
  osascript -e "display alert \"$1\" message \"$2\"" >/dev/null 2>&1
}

# ---------------------------------------------------------------- permission
# The project sits under Desktop, which macOS protects. An app is refused until the
# person allows it once. Ask for the folder before anything else touches it, so a refusal
# produces one clear instruction rather than five confusing errors further down.
if ! ls "$ROOT" >/dev/null 2>&1; then
  say "cannot read $ROOT"
  choice=$(osascript <<'OSA' 2>/dev/null
display alert "Job App needs permission" message "macOS is blocking Job App from reading its own folder on your Desktop.

Open Settings, turn on Job App in the list, then open Job App again. You only do this once." buttons {"Not now", "Open Settings"} default button "Open Settings"
OSA
)
  case "$choice" in
    *"Open Settings"*) open "x-apple.systempreferences:com.apple.preference.security?Privacy_SystemPolicyAllFiles" ;;
  esac
  exit 1
fi

cd "$ROOT" || { alert "Job App" "Cannot enter its own folder."; exit 1; }

PORT="$("$PY" - <<'PORTPY' 2>/dev/null
import os, re, pathlib
port = "8100"
env = pathlib.Path(".env")
if env.exists():
    m = re.search(r"^PORT\s*=\s*(\d+)", env.read_text(), re.M)
    if m:
        port = m.group(1)
print(os.environ.get("PORT", port))
PORTPY
)"
[ -n "$PORT" ] || PORT=8100
say "port: $PORT"

listening() {
  "$PY" -c "import socket,sys; s=socket.socket(); s.settimeout(0.3); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)"
}

# Already up, from an earlier launch or from run.command. Just bring the dashboard forward.
if listening; then
  say "already listening, opening the browser only"
  open "http://127.0.0.1:${PORT}/job"
  exit 0
fi

if ! "$PY" -c "import fastapi, uvicorn, docx, sqlalchemy" >/dev/null 2>&1; then
  say "installing dependencies"
  if ! "$PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt >>"$LOG" 2>&1; then
    alert "Job App did not start" "Could not install the Python packages it needs. See Library/Logs/JobApp.log"
    exit 1
  fi
fi

# Open the browser the moment the server answers, never before. A tab that lands on a
# connection error and has to be reloaded is the difference between this and a script.
(
  for _ in $(seq 1 100); do
    if listening; then
      open "http://127.0.0.1:${PORT}/job"
      exit 0
    fi
    sleep 0.2
  done
  alert "Job App" "The server did not come up within 20 seconds. See Library/Logs/JobApp.log"
) &

say "starting the server"
exec "$PY" main.py >>"$LOG" 2>&1
