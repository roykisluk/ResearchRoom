# ResearchRoom

The shared research-room engine (`room/`), its landing page (`landing/`) and the made-up example (`example/`).
Run `tools/pre-push` (all tests, plus the clean check: no personal names in files or commit messages) before every
push; it is installed as the git hook. The public site (`tools/site.sh --deploy`) is the landing page at the root and
the example room at `/demo/`.

## Lesson log
- **2026-11-27 — data-site wording removed; example room public.** Symptom: the repo and site described where the
  results came from, unfit for a public link. Root cause: the engine was written for one project and hard-coded that
  project's folder name (the scripts behind `out/<run>/RELEASE/` figures), its script ids and labels. Fix: say
  "results" everywhere; the folder is now the config key `results_code` (default `code/results`), and that project
  sets its own folder in its `.room/config.toml`, so its 19 results figures still link their scripts (checked by an
  export). Script ids are now `results:<script>` (no project stored the old ids). Git history still holds the old
  wording (a rewrite is not decided), and the clean check does not look for it: run `git grep -il` for the old words
  by hand before a push that touches prose. Then the site was redeployed and the custom domain and the pages.dev host
  were taken out of the Access app; per-deploy `*.<project>.pages.dev` hosts stay behind the login because older
  deployments hold the old wording. Public example: the site's `/demo/`.
- **2026-11-27 — the clean check also reads unpushed commits.** A lesson entry naming the owner and the project was
  refused at push (`history:` hits). Fix: amend the unpushed commit; keep names out of this repository.
