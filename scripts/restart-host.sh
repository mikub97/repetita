#!/usr/bin/env bash
# Restart whatever is serving repetita, exactly as it was started.
#
# Repetita can run standalone (`repetita serve`) or mounted inside a host
# application (docs/hosting.md), so this does not assume what the command is:
# it reads the command line and working directory off the running process and
# starts it again the same way.
#
# It is keyed on the port, which is the only thing that reliably identifies a
# running server here. Holding the study database open would have been a nicer
# signal -- no configuration at all -- but repetita opens it per request and
# closes it at teardown, so between requests nobody has it open. The port is
# remembered in .repetita-host after the first run, so it is asked for once.
#
# Why a script at all: the host serves repetita live from this working tree, so
# picking up a code change means a restart, and a restart may migrate the study
# database. Doing that by hand is three commands, one of which is `kill`, and
# the one worth never forgetting is the backup.
set -euo pipefail
cd "$(dirname "$0")/.."

port=""
cmd=""
dir=""
timeout=30

usage() {
  cat <<'USAGE'
usage: scripts/restart-host.sh [--port N] [--cmd "..."] [--dir PATH] [--timeout S]

With nothing: finds the process holding the study database open, stops it, and
starts it again with the same command in the same directory.

  --port N      port to wait for (default: read from the running command line)
  --cmd "..."   how to start it, when nothing is running to copy
  --dir PATH    directory to start it from, with --cmd
  --timeout S   how long to wait for it to answer (default 30)

The database is REPETITA_DB, or ./data/repetita.db.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --cmd) cmd="$2"; shift 2 ;;
    --dir) dir="$2"; shift 2 ;;
    --timeout) timeout="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "restart-host: unknown option $1" >&2; usage >&2; exit 2 ;;
  esac
done

db="${REPETITA_DB:-$PWD/data/repetita.db}"
[ -f "$db" ] || { echo "restart-host: no study database at $db" >&2; exit 1; }

# Asked for once, then remembered. The file is gitignored: which port someone
# runs their own host on is not a property of the project.
remembered=".repetita-host"
[ -n "$port" ] || port="${REPETITA_HOST_PORT:-}"
[ -n "$port" ] || { [ -f "$remembered" ] && port="$(cat "$remembered")"; }
[ -n "$port" ] || {
  echo "restart-host: which port is it on? Pass --port N once and it is remembered." >&2
  echo "  e.g. scripts/restart-host.sh --port 5116" >&2
  exit 1
}

# --- who is serving it ----------------------------------------------------
# Everything about the running process is read before anything is killed. If we
# cannot describe how to start it again, better to find that out while it is
# still up.
pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true)"

if [ -n "$pid" ]; then
  [ -n "$cmd" ] || cmd="$(ps -o command= -p "$pid")"
  [ -n "$dir" ] || dir="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  echo "serving now:  pid $pid on port $port"
  echo "  command:    $cmd"
  echo "  directory:  ${dir:-?}"
else
  echo "nothing is listening on port $port."
  [ -n "$cmd" ] || {
    echo "restart-host: pass --cmd \"...\" (and --dir) to start it cold." >&2
    exit 1
  }
fi

[ -n "$cmd" ] || { echo "restart-host: could not work out how to start it" >&2; exit 1; }
[ -n "$dir" ] || dir="$PWD"

# --- back up first --------------------------------------------------------
# A restart picks up new code, and new code may migrate the schema. Content is
# rebuilt from the course files, but `card_state` and `review_log` are not
# recoverable from anything (CLAUDE.md rule 2), so the copy is taken every time
# rather than when someone judges it risky.
backup="${db%.db}-$(date +%Y%m%d-%H%M%S)-pre-restart.db"
cp "$db" "$backup"
echo "backup:       $backup"

before="$(sqlite3 "$db" \
  "SELECT (SELECT value FROM meta WHERE key='schema_version'), \
          (SELECT COUNT(*) FROM review_log), (SELECT COUNT(*) FROM card_state);" 2>/dev/null || echo "?|?|?")"

# --- stop -----------------------------------------------------------------
if [ -n "$pid" ]; then
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  # SIGKILL only if it ignored SIGTERM. A server mid-write deserves the chance
  # to close the database cleanly first.
  if kill -0 "$pid" 2>/dev/null; then
    echo "  did not stop on SIGTERM; sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
  fi
  echo "stopped:      pid $pid"
fi

# --- start ----------------------------------------------------------------
log="${TMPDIR:-/tmp}/repetita-host.log"
( cd "$dir" && nohup sh -c "$cmd" >"$log" 2>&1 & )
echo "starting:     log at $log"

ok=""
for _ in $(seq 1 "$((timeout * 2))"); do
  sleep 0.5
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://127.0.0.1:$port/" || true)"
  case "$code" in 2??|3??) ok="$code"; break ;; esac
done

after="$(sqlite3 "$db" \
  "SELECT (SELECT value FROM meta WHERE key='schema_version'), \
          (SELECT COUNT(*) FROM review_log), (SELECT COUNT(*) FROM card_state);" 2>/dev/null || echo "?|?|?")"

echo
echo "               schema | reviews | card states"
echo "  before:      ${before}"
echo "  after:       ${after}"
[ "$before" = "$after" ] || echo "  (the schema moved -- that is a migration, and the backup above predates it)"

if [ -z "$ok" ]; then
  echo
  echo "restart-host: it did not answer within ${timeout}s. Last of $log:" >&2
  tail -20 "$log" >&2
  exit 1
fi

printf '%s' "$port" > "$remembered"

echo
echo "up on http://127.0.0.1:$port/ (HTTP $ok)"
