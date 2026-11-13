# Changelog

One line for each change that asks a project to do something. Fetch updates with `git pull`.

## 2026-11-13
- First version: the engine (`room/dashboard.py`, `room/agent.py`, `room/project.py`, `room/site/`) and
  `tools/clean-check.sh`. A project runs it as `python3 ../ResearchRoom/room/dashboard.py --project .`, keeps its
  dashboard data in `.room/`, and sets its own values in `.room/config.toml`.
- `--publish` now checks the new deployment's own address after the deploy and exits with an error unless Access or
  the middleware closes it. The export names every output that is not in the room. Nothing to do in a project.
