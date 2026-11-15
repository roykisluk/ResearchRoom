#!/usr/bin/env bash
# room-server.sh and room-tunnel.sh against stub tmux/uv/cloudflared and throwaway repos: what each run would start or kill
set -u  # usage: tools/room-scripts-test.sh (pre-push runs it)
SB=$(mktemp -d) SRC=$(cd "$(dirname "$0")/.." && pwd); trap 'rm -rf "$SB"' EXIT; mkdir -p "$SB/u/.local/bin" "$SB/u/.cache"; cd "$SB"
G() { git -c user.email=t@t -c user.name=t "$@"; }
for r in eng cl; do G init -q --bare $r.git; G clone -q $r.git $r 2>/dev/null; done
mkdir -p eng/tools && cp "$SRC"/tools/room-server.sh "$SRC"/tools/room-tunnel.sh eng/tools/
(cd eng && G add -A && G commit -qm e && G push -q origin HEAD 2>/dev/null && G branch -q -u origin/$(git branch --show-current))
(cd cl && echo x > f && G add -A && G commit -qm c && G push -q origin HEAD 2>/dev/null && G branch -q -u origin/$(git branch --show-current))
cat > u/.local/bin/tmux <<EOF
#!/bin/sh
echo "tmux \$*" >> $SB/calls
[ -n "\${TUNNEL_TOKEN:-}" ] && echo "tmux-env-has-token" >> $SB/calls  # tmux hands a new server its client's environment
case "\$1" in has-session) [ -f $SB/session ] ;; kill-session) rm -f $SB/session ;; new) touch $SB/session ;; esac
EOF
printf '#!/bin/sh\nexit 0\n' > u/.local/bin/uv; cp u/.local/bin/uv u/.local/bin/cloudflared; chmod +x u/.local/bin/*
printf 'ACCESS_AUD=aud1\nTUNNEL_TOKEN=tok\n' > env
run() { HOME=$SB/u PATH=$SB/u/.local/bin:/usr/bin:/bin bash "$@" 2>&1; }
fails=0; ok() { if eval "$1"; then echo "PASS $2"; else echo "FAIL $2"; cat calls; fails=1; fi; }
advance() { (cd eng && echo $RANDOM >> tools/x && G add -A && G commit -qm up && G push -q origin HEAD 2>/dev/null && G reset -q --hard HEAD~1); }
: > calls; run eng/tools/room-server.sh "$SB/cl" r1 "$SB/env" h.example 8790 > out1
ok "grep -q \"new -d -s r1 -c $SB/cl .*dashboard.py' --claude --public 'h.example' --port '8790'\" calls" "server: starts the room with host and port"
ok "grep -q \"uv' run --python /usr/bin/python3\" calls && [ -s u/.cache/r1-engine ]" "server: full uv path, stamp written"
ok "grep -q \"ACCESS_AUD='aud1' \" calls && ! grep -q tok calls" "server: the room gets ACCESS_AUD and not the tunnel's token"
: > calls; run eng/tools/room-server.sh "$SB/cl" r1 "$SB/env" h.example 8790 > out2
ok '! grep -qE "kill-session|new " calls' "server: running, nothing moved -> left alone"
advance; : > calls; run eng/tools/room-server.sh "$SB/cl" r1 "$SB/env" h.example 8790 > out3
ok 'grep -q "kill-session -t =r1" calls && grep -q "new -d -s r1" calls && grep -q "restarting it" out3' "server: engine moved, idle -> restarts"
advance; echo dirty >> cl/f; : > calls; run eng/tools/room-server.sh "$SB/cl" r1 "$SB/env" h.example 8790 > out4
ok '! grep -q "kill-session" calls' "server: a tracked file edited -> no restart"
rm -f session; : > calls; run eng/tools/room-tunnel.sh "$SB/env" > out5
ok "grep -q \"new -d -s room-tunnel .*cloudflared' tunnel --no-autoupdate run\" calls" "tunnel: starts, cloudflared by full path"
ok '! grep -q tmux-env-has-token calls' "tunnel: tmux is not started with the token in its environment (every window would inherit it)"
: > calls; run eng/tools/room-tunnel.sh "$SB/env" > out6; ok '! grep -q "new " calls' "tunnel: running -> nothing"
rm -f session; printf 'ACCESS_AUD=\n' > env2; : > calls; run eng/tools/room-server.sh "$SB/cl" r2 "$SB/env2" h 8791 > out7
ok '! grep -q "new " calls' "server: no ACCESS_AUD -> does not start"
# a room commit not yet pushed is not a move: no restart every 5 minutes
git -C cl checkout -q -- f; touch session; run eng/tools/room-server.sh "$SB/cl" r1 "$SB/env" h.example 8790 > out8
(cd cl && echo y > g && G add -A && G commit -qm local)
: > calls; run eng/tools/room-server.sh "$SB/cl" r1 "$SB/env" h.example 8790 > out9
ok '! grep -q "kill-session" calls' "server: the room's clone ahead of origin -> no restart loop"
# no env file: the running room is left up, and the log says why nothing started
advance; : > calls; run eng/tools/room-server.sh "$SB/cl" r1 "$SB/nope" h.example 8790 > out10
ok '! grep -q "kill-session" calls && grep -q "not set up" out10' "server: env file missing -> room left running, a log line"
# an engine pull that fails is not stamped as up to date, so the next run tries again
echo local >> eng/tools/x; : > calls; run eng/tools/room-server.sh "$SB/cl" r1 "$SB/env" h.example 8790 > out11
ok '[ "$(cat u/.cache/r1-engine)" != "$(git -C eng rev-parse @{u})" ] && grep -q "did not pull" out11' "server: a failed engine pull is not stamped as current"
git -C eng checkout -q -- tools/x
exit $fails
