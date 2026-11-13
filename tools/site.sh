#!/usr/bin/env bash
# The project's website: the landing page (landing/) at the root, and the example project's room at /demo/.
#   tools/site.sh DIR        build it into DIR, to look at: python3 -m http.server -d DIR
#   tools/site.sh --deploy   build it and deploy it to the Cloudflare Pages project "researchroom", which is public:
#                            every number in the example is made up. Needs CLOUDFLARE_API_TOKEN (Pages Edit) and
#                            CLOUDFLARE_ACCOUNT_ID in the environment, and npx.
# Needs uv and pdftoppm. The example is drawn again in a copy outside git (example/make.py), where the room dates each
# plot by the sitting that brought it back. The landing page's links to this repository come from the git remote at
# build time (__REPO__), so no address is written into the repository.
set -euo pipefail
t=$(mktemp -d); trap 'rm -rf "$t" "$HOME/.cache/northvale-png"' EXIT  # the example's name is Northvale, and so is its PNG cache
case "${1:-}" in
  --deploy) out=$t/dist ;;
  ""|-*) echo "usage: tools/site.sh DIR | --deploy" >&2; exit 2 ;;
  *) mkdir -p "$1"; out=$(cd "$1" && pwd) ;;
esac
cd "$(dirname "$0")/.."
cp -r example "$t/example"
uv run -q --with matplotlib python "$t/example/make.py" > /dev/null
python3 room/dashboard.py --project "$t/example" --export "$out/demo" > /dev/null
repo=$(git remote get-url origin | sed -E 's#://[^@/]*@#://#; s#^git@([^:]+):#https://\1/#; s#\.git$##')  # never a credential
cp -r landing/. room/site/favicon.ico room/site/apple-touch-icon.png "$out/"
sed "s#__REPO__#$repo#g" landing/index.html > "$out/index.html"
if [ "$1" = --deploy ]; then
  cd "$t"  # wrangler writes its own files (.wrangler/, a config, .gitignore lines) where it runs: not into the repository
  npx --yes wrangler pages deploy dist --project-name researchroom --branch main --commit-dirty=true
else
  echo "built $out"
fi
