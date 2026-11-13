#!/usr/bin/env bash
# Nothing personal goes into this repository: not in a file, and not in any commit of its history.
#   tools/clean-check.sh             every tracked file, and every commit message and diff in the history (LICENSE excepted)
#   tools/clean-check.sh PATH ...    those files only
# Built-in patterns: an e-mail address (the reserved example.org, example.com and example.net and the two no-reply
# addresses of commit trailers excepted), a home
# folder path. Plus one extended regular expression per line of the deny-list, which holds the personal strings
# themselves and so lives outside the repository: ~/.config/researchroom/denylist, or $ROOM_DENYLIST.
# Prints file:line:match for each hit and exits 1; exits 0 when clean; exits 2 when a pattern is not a valid
# expression, since grep would then report nothing. A missing deny-list is a warning: the built-in patterns alone
# prove little. Binary files (figures, icons) are not read.
set -u
deny=${ROOM_DENYLIST:-$HOME/.config/researchroom/denylist}
pat=$(mktemp); trap 'rm -f "$pat"' EXIT
printf '%s\n' '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}' '/ho''me/' '/Us''ers/' > "$pat"  # split, so this file does not match itself
if [ -f "$deny" ]; then
  grep -v -E '^(#|[[:space:]]*$)' "$deny" >> "$pat"
else
  echo "clean-check: no deny-list at $deny: only the built-in patterns are checked" >&2
fi
grep -E -f "$pat" /dev/null; [ $? -eq 2 ] && { echo "clean-check: a line of $deny is not a valid expression" >&2; exit 2; }
ok='@example\.(org|com|net)$|:noreply@anthropic\.com$|@users\.noreply\.github\.com$'
hits() { grep -I -n -o -E -f "$pat" "$@" | grep -v -E "$ok"; }
if [ $# -gt 0 ]; then
  out=$(hits -H -- "$@")
else
  cd "$(dirname "$0")/.." || exit 2
  out=$( { git ls-files -z | grep -z -v -x 'LICENSE' | xargs -0 -r grep -I -n -o -H -E -f "$pat" --
           git log -p --format=%B -- . ':(exclude)LICENSE' | grep -I -n -o -H --label=history -E -f "$pat"; } \
         | grep -v -E "$ok")
fi
[ -z "$out" ] && exit 0
printf '%s\n' "$out"
exit 1
