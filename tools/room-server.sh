#!/usr/bin/env bash
# A project's Claude room on a server (dashboard.py --claude --public HOST), reached through the tunnel that
# room-tunnel.sh keeps up. Cron runs this every 5 minutes. It starts the room in tmux NAME when that session is gone,
# and restarts it when the clone's main or the engine has moved and the room is idle: no tracked file edited (the
# room's edits reach main through its commit card), and no Claude run in the clone, unless the run is over 6 hours old.
#   */5 * * * * $HOME/ResearchRoom/tools/room-server.sh CLONE NAME ENV_FILE HOST PORT >> $HOME/.cache/NAME.log 2>&1
# CLONE: a clone of the project kept for the room alone. ENV_FILE holds ACCESS_AUD, the tag of the Access app that
# covers HOST. HOST: the room's address, a public hostname of the tunnel pointing at http://127.0.0.1:PORT.
set -euo pipefail
export PATH=$HOME/.local/bin:$PATH
clone=$1 name=$2 env=$3 host=$4 port=$5
engine=$(cd "$(dirname "$0")/.." && pwd)
stamp=$HOME/.cache/$name-engine log=$HOME/.cache/$name.log
cd "$clone"
if tmux has-session -t "=$name" 2>/dev/null; then
  git fetch -q origin 2>/dev/null || exit 0
  git -C "$engine" fetch -q origin 2>/dev/null || true
  up=$(git -C "$engine" rev-parse '@{u}' 2>/dev/null || true)
  [ "$(git rev-parse HEAD)" != "$(git rev-parse '@{u}')" ] || [ "$up" != "$(cat "$stamp" 2>/dev/null)" ] || exit 0
  [ -z "$(git status --porcelain --untracked-files=no)" ] || exit 0
  for p in $(pgrep -f stream-json || true); do
    [ "$(readlink "/proc/$p/cwd" || true)" = "$PWD" ] || continue
    [ "$(ps -o etimes= -p "$p" 2>/dev/null || echo 0)" -ge 21600 ] || exit 0
    echo "$(date -Is) ignoring Claude run $p, alive over 6 hours"
  done
  echo "$(date -Is) main is at $(git rev-parse --short '@{u}'), the engine at ${up:0:7}; one moved and the room is idle: restarting it"
  tmux kill-session -t "=$name"
fi
[ -f "$env" ] || exit 0  # not set up yet
# only ACCESS_AUD reaches the room: the file may also hold the tunnel's token, which the room's Claude must not inherit
aud=$(sed -n 's/^ACCESS_AUD=//p' "$env" | head -1 | tr -d "\"'")
[ -n "$aud" ] || exit 0  # dashboard.py --public refuses to start without it
git pull --ff-only -q || echo "$(date -Is) the clone did not pull; starting on $(git rev-parse --short HEAD)"
# this script lives in the engine: bash goes on reading the old text of a file the pull replaced, so run the new one
self=$(git -C "$engine" rev-parse HEAD:tools/room-server.sh)
git -C "$engine" pull --ff-only -q || echo "$(date -Is) the engine did not pull; starting on $(git -C "$engine" rev-parse --short HEAD)"
[ "$self" = "$(git -C "$engine" rev-parse HEAD:tools/room-server.sh)" ] || exec "$engine/tools/room-server.sh" "$@"
# the stamp is the engine's upstream commit, which the check above compares; a clone ahead of origin would never match
{ git -C "$engine" rev-parse '@{u}' 2>/dev/null || git -C "$engine" rev-parse HEAD; } > "$stamp"
echo "$(date -Is) starting room $name on $(git rev-parse --short HEAD), engine $(git -C "$engine" rev-parse --short HEAD)"
uv=$(command -v uv) || { echo "$(date -Is) uv is not on PATH"; exit 1; }
# tmux starts a window from its server's environment: the window gets ACCESS_AUD and uv's full path from here.
# The system Python: uv's own (3.13+) rejects a TLS-inspecting proxy's CA that has no Authority Key Identifier when it
# fetches Access's keys.
tmux new -d -s "$name" -c "$PWD" "ACCESS_AUD='$aud' '$uv' run --python /usr/bin/python3 --with claude-agent-sdk --with 'pyjwt[crypto]' python '$engine/room/dashboard.py' --claude --public '$host' --port '$port' >> '$log' 2>&1"
