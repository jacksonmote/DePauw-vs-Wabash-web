# AGENTS.md

Rules for any agent or contributor changing this repo. README.md has the detail; this file is the short list of what must not break and how to check a change.

## What this is

A fan site (https://depauwvswabash.com/) comparing DePauw University and Wabash College: all-time head-to-head athletics records, merged from both schools' own history pages by a scraper that runs on GitHub Actions, plus a hand-curated alumni and endowment page. `scraper/render.py` writes static HTML. Unofficial; not affiliated with either school.

Read the README sections you touch: "How the data updates", "Reading and adjusting the logic", "Review hold", "Snapshots and archiving", "Adding a sport", "Data format", "Contributing", and "Where things are" (the file map).

## Before you call a change done

Commands are in README "Run it locally". Run them from the repo root.

1. `python -m pytest -q tests`
2. `python scraper/render.py`
3. `node --check static/easter-eggs.js`, then html-validate on all seven built pages (the commands in README and `.github/workflows/checks.yml`).
4. The Mermaid diagram check (same two places). GitHub draws with a newer Mermaid than the CLI, so after a diagram change also look at the README on GitHub.
5. Visible change: look at the result at 375px wide and at desktop width, in light and dark mode, and show it.
6. Update the README in the same PR. A change to the pipeline, data files, workflows, triggers or game-day behavior also updates the matching README diagram (and the workflow table). Keep the how-it-works text, module docstrings, workflow comments, issue templates and `scraper/sources.json` `_comment` in step with the code.
7. Tests gate every deploy: a failing test stops all updates (scheduled, push and manual). Never write a test that depends on today's date, the current season or a live page.
8. Never edit a pinned total in `tests/test_overrides.py` just to pass. Check each changed game first.

## Invariants (do not break)

Data
- Each school's page is accepted only when its parsed W-L-T, home/away records and current streak equal that page's own summary and no row repeats. Never loosen this, and never check one school's page against the other's. The page's total points/goals/runs is a warning only.
- A page that fails keeps that school's last good games for that sport and the run exits non-zero; everything that passed still updates. A page with fewer games than its saved copy is rejected unless `--allow-shrink`.
- Review hold: a change to a historical game (a season before the sport's current one) is held in `data/pending-changes.json` until a person approves it. Never write such a change straight into `depauw.json` or `wabash.json`. `--allow-shrink` does not lift the hold.
- When the schools disagree, DePauw's version counts and Wabash's is shown beside it. This is a convention, not a finding. Never hide or silently pick a side.
- Prefer a ruling in `data/overrides.json` (with a reason) to changing a merge rule. A ruling that no longer finds its rows fails that sport's merge on purpose. A merge rule change affects every sport: follow README "When you want to change a rule or number".
- `series.json` is always the merge of `depauw.json`, `wabash.json` and `overrides.json`. Do not hand-edit it. Data files are rewritten only when their content changes.
- Parser cases that each broke or nearly broke: half-point scores (`172.5`; a whole score stays an int); a result with no score (`score_known: false`, left out of every score fact); result letter and scores that disagree (trust the letter, swap, `score_order_reversed`); Wabash placeholder dates (Jan 1 to 3) and out-of-season dates (`date_known: false`: never print `date`, never the "most recent" game). These flags are written only when they apply.
- Sources: DePauw's opponent id for Wabash is 215, Wabash's for DePauw is 4. Wabash's slugs differ: men's tennis is `tennis`, swimming and diving is `swimming-and-diving`. Identify the project in the User-Agent, wait a few seconds between requests, fetch no more often than the schedule needs.
- Scope: men's head-to-head sports only (Wabash is all-male). Swimming and diving counts dual meets only; invitationals, relays and championships never count. Ask the maintainer before adding any other individual or multi-team sport.
- Check values for the current data (update them, and the pinned tests, only when a new result changes them): football merges to 131 games, 59-63-9 (DePauw-Wabash), equal to both pages' own records; the Monon Bell era (1932 season on) is DePauw 43, Wabash 44, 6 ties through 2025; baseball merges to 314 games, 155-156-3, equal to Wabash's page; all sports 918 games, DePauw 541, Wabash 357, 20 ties, 15 disputed scores, none of which changes a winner. `tests/test_overrides.py` pins the per-sport totals.

Pipeline
- Tests run before the scrape in `update-and-deploy.yml`. A scraper failure is tolerated (validated fallback data publishes) and the `report` job turns the run red after the deploy. A push made with the workflow's token does not start another run.
- Render is a hard gate: `render.py` runs after the scrape and before the data commit, and a failure stops the build before any commit, upload or deploy. The HTML validation then runs in production after the render, with the same pinned `setup-node`, `html-validate` version and seven-page list as `checks.yml`; it, the Node setup, and the production script syntax check are non-blocking. The syntax check runs only if Node setup succeeds; pull-request checks remain the hard syntax gate. A failed or skipped validation writes a `::warning` annotation and a run-summary note, the site still publishes, and the `report` job turns the run red after the deploy and says which problem it was.
- The frequent cron (`7,37 * * * *`) must equal `gameday.FREQUENT_CRON`. GitHub can delay or skip scheduled runs and switches them off after 60 days without repo activity.
- Snapshots: the Wayback calls stay in the last `archive` job, which can never fail the run. A retry merges links and never overwrites them. The limits are the `SAVE_*` constants in `scraper/snapshot.py`.
- `render.py --now` is for previews and tests; no workflow may use it.

Site
- No inline JavaScript or styles and no third-party requests. The one currently approved client-side exception is the deferred, same-origin `static/easter-eggs.js` title entrance on `/`; it opts into `script-src 'self'` only on that page. All six other generated pages remain script-free under the base CSP (`default-src 'none'`). Do not add other client-side code without a maintainer decision. No diagrams on the public site.
- Every number on a page comes from the data or a named constant. Never hardcode a count, year or size word ("twice").
- No school logos, wordmarks, seals or official mascot art, on pages or in generated images. The one approved original generic tiger drawing is a small easter egg in `static/easter-tiger.svg`; it is not official mascot art and does not appear in generated images. Keep the site non-commercial (no ads, affiliate links, donations or sales) and not official-looking. Every page keeps the "unofficial fan site, not affiliated" note.
- Text rules: curly apostrophe (’) in visible text, serial comma in lists of three or more, AP-style locations (display only), dates like "Oct 29, 2026". Each page's footer comes from `site_footer()`; add footer content as a block there, never as page HTML.
- Alumni and endowment data is hand-curated, validated on every build and reviewed in PRs. No overall alumni score. Endowment per student uses one basis (pooled endowment net assets) and the 5% "about even" rule. Do not return to the unadjusted totals without a maintainer decision.

## Repo hygiene

- Commit only the project's own files. No plans, working notes, credentials, personal details or local file paths. Do not add files that are not part of the project unless the maintainer asks. `docs/` holds one older plan; do not add to it.
- Pin every GitHub Action to a full commit SHA with a version comment, or the workflow fails.
- Workflows use `pull_request`, never `pull_request_target`, request the minimum permissions per job and use no secrets.
- Tests that run the committed rulings against `tests/fixtures/` fail if a fixture is refreshed with changed rows. Refresh them together with the rulings.

## Collaboration

- Branch from the latest `main`. Any descriptive name works (`feat/…`, `fix/…`, `docs/…`, `codex/…`).
- `main` moves (the data bot commits to it, others merge). Before pushing, `git fetch origin` and bring the branch up to date: rebase if it was never pushed, merge `origin/main` if it was. Keep both sides' changes, then re-run the checks. The "Protect main" ruleset requires an up-to-date branch.
- One PR per change. Never push to `main`. Never force-push a shared branch.
- The required status check is the `checks` job in `checks.yml` (matched by job name, not file name). Renaming the job needs a matching ruleset change by the maintainer.
- The maintainer reviews and merges. Pull requests from forks need approval before workflows run.
