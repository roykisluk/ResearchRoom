# ResearchRoom

A dashboard for an empirical research project. It reads the figures and tables your scripts already write, and shows
them as a *room*: one card per plot, its versions and variants behind switches, grouped by the date you read each
result. You run it on your own project; nothing is uploaded unless you publish it yourself.

## What you need
- Python 3.11 or newer, and poppler (`pdftoppm`) to show PDF figures: `apt install poppler-utils` or `brew install poppler`.
- Nothing else for local use: the server is standard library only.
- Only to publish: node (`npx wrangler`). Only for the Claude panel: `claude-agent-sdk`, and `pyjwt` for the web room.

## Try it
```
git clone <this repository's URL> ResearchRoom
python3 ResearchRoom/room/dashboard.py --project ResearchRoom/example
```
Open http://127.0.0.1:8765/room. The example holds three figures, one table and one finding, all from made-up numbers
(`example/make.py`). For your own project, clone this repository beside it and run, from the project folder:
```
python3 ../ResearchRoom/room/dashboard.py --project .
```

## What the dashboard expects from a project
- **Required:** a `figures/` or a `tables/` folder, with outputs named `N_name_vK.ext`: `N` is a script number of two
  or three digits and `K` the version, as in `02_shares_v1.png`. The newest version is shown; older ones are flagged
  `superseded` at `/all`.
- **Optional, and switched on by being there:** `code/local/N_name_vK.py` (the script that writes the outputs
  numbered `N`: its link, and a flag when an output is older than its script); `docs/findings.md` (the overview at `/`;
  format in `example/docs/findings.md`); `docs/runs/readout_<date>_<label>.md` and `out/N_name_vK/RELEASE/` (results
  that come back from a restricted-data site, dated by the commit that added the read-out); `docs/log/`.
  An output whose number no script carries is a plot of its own. We note one limit: a file not named `N_name_vK.ext`,
  or one whose number several scripts carry while none matches its first word, is listed at `/all` only; the export
  names each such file.
- **Files a figure script may write beside an output** (`<stem>` is the file name without its extension):
  `<stem>.txt`, the caption; `<stem>.eq.json`, the estimating equation as `{"eq": "<TeX>", "notes": [...]}`;
  `<stem>.variant.json`, the family a figure belongs to and its option on each switch; `<stem>.ptype`, the plot type.

## The project's own data: `.room/`
The dashboard keeps what belongs to your project in `.room/`, inside the project: titles, tags, tasks and the like as
tab-separated files, your icons, and `config.toml`. Every setting is optional. `example/.room/config.toml` lists all of
them with a comment each; a key that is not on that list stops the dashboard with a message that names it.

## Publishing (optional)
`--export DIR` writes the room as static files that any static host can serve. The built-in recipe is Cloudflare Pages
behind Cloudflare Access:
1. Create a Pages project and an Access application that covers its hosts (plus a Bypass rule for `/favicon.ico` and
   `/apple-touch-icon.png`, which browsers fetch without a login).
2. Fill in `[hosted]` in `.room/config.toml`.
3. Run `python3 ../ResearchRoom/room/dashboard.py --project . --publish`.

Publishing refuses to deploy unless each listed host still redirects to your Access login. After a deploy it also
checks the deployment's own address (`<hash>.<project>.pages.dev`, which every deploy gets): Access must cover it, or
the site's middleware must refuse it. If neither holds, it says so and exits with an error. `.room/site/` is the publish
folder, a full copy of the export: add it to your `.gitignore`. We note one limit: file paths on the published copy keep
your script numbers and names; only the labelled name fields are blanked.

## The Claude panel (optional)
`uv run --with claude-agent-sdk python ../ResearchRoom/room/dashboard.py --project . --claude` adds a panel that
answers questions about a plot with Claude Code. It runs on the Claude Code login of whoever starts the server, with
that person's settings, and it can edit, commit and push in the project. It is for the project's owner only: do not
expose it to anyone else. Collaborators get the published copy, which has no panel.

## Updating
`git pull` in your clone. `CHANGELOG.md` has one line for each change that asks you to do something.

## Developing
`tools/pre-push` runs the self-tests, exports the example, and runs `tools/clean-check.sh`, which keeps personal
details out of this repository. Install it once: `ln -s ../../tools/pre-push .git/hooks/pre-push`. MIT licence.

Comments in the code cite dated notes (`docs/plans/…`, `DECISIONS`, `docs/lessons.md`, dates such as 10-20). They are the
design notes of the project the dashboard was first built in, and they are not in this repository.
