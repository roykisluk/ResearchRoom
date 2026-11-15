#!/usr/bin/env bash
# One Cloudflare tunnel for every Claude room on this server. Cron runs this every 5 minutes; it starts the tunnel in
# tmux "room-tunnel" when that session is gone, and does nothing otherwise. Each room is a public hostname of this one
# tunnel (set in Cloudflare's dashboard), pointing at the port its room-server.sh line names.
#   */5 * * * * $HOME/ResearchRoom/tools/room-tunnel.sh ENV_FILE >> $HOME/.cache/room-tunnel.log 2>&1
# ENV_FILE holds TUNNEL_TOKEN, the tunnel's run token (written by hand; never printed here).
set -euo pipefail
export PATH=$HOME/.local/bin:$PATH
env=$1
tmux has-session -t "=room-tunnel" 2>/dev/null && exit 0
[ -f "$env" ] || exit 0  # not set up yet
set -a; . "$env"; set +a
[ -n "${TUNNEL_TOKEN:-}" ] || exit 0
bin=$(command -v cloudflared) || { echo "$(date -Is) cloudflared is not on PATH"; exit 1; }
echo "$(date -Is) starting the tunnel"
# tmux starts a window from its server's environment, not this script's: the window reads the file itself and names
# cloudflared by its full path
tmux new -d -s room-tunnel "set -a; . '$env'; '$bin' tunnel --no-autoupdate run >> '$HOME/.cache/room-tunnel.log' 2>&1"
