# Changelog

One line for each change that asks a project to do something. Fetch updates with `git pull`.

## 2026-11-27
- The scripts behind `out/<run>/RELEASE/` figures are looked for in `code/results/` unless `.room/config.toml` sets
  `results_code` to another folder; their script ids are now `results:<script>`. To do: a project keeping those
  scripts elsewhere sets `results_code`; links to the old script ids change.

## 2026-11-21
- The hosted copy asks its room for the owner's archive and importance marks (`GET /api/marks`) and shows those over
  its `index.json`, which is only as new as the last publish: an item archived on the shared site no longer comes back
  on a refresh. To do: publish the hosted copy once, so it carries the new `app.js`.

## 2026-11-18
- A Brief mode, `/room?brief=<read date>`: one sitting's results ask by ask, from `docs/runs/takeaways_<read date>.md`
  with an `ask:` line per point and a `## Still open` section (README, "A sitting's brief"). The owner's questions
  there go to one thread per point. To do: nothing; older takeaways files list every point as an ask.
- The Brief has a chat column for the owner: one resumable Claude chat per sitting, kept in `.room/chats/` (ignored by
  git), not in the Q&A. To do: nothing.

## 2026-11-13
- First version: the engine (`room/dashboard.py`, `room/agent.py`, `room/project.py`, `room/site/`) and
  `tools/clean-check.sh`. A project runs it as `python3 ../ResearchRoom/room/dashboard.py --project .`, keeps its
  dashboard data in `.room/`, and sets its own values in `.room/config.toml`.
- `--publish` now checks the new deployment's own address after the deploy and exits with an error unless Access or
  the middleware closes it. The export names every output that is not in the room. Nothing to do in a project.
- An option is written in full (`--project`, not `--proj`), and a config value of the wrong type (a date that is
  not `YYYY-MM-DD`, a string where a list goes) stops the engine with one line naming the key. To do: nothing, unless
  a script of yours abbreviates an option.
