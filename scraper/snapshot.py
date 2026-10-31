"""
Data snapshots kept outside the git history: a GitHub Release tagged data-YYYY-MM-DD, plus a request to the
Internet Archive to save each source page. Used by .github/workflows/snapshot.yml, which runs twice a year
(after the spring seasons and after the fall seasons) and on demand.

  python scraper/snapshot.py check              print changed=true/false (also to $GITHUB_OUTPUT): has the
                                                data changed since the newest data-* release?
  python scraper/snapshot.py build --out DIR    write the release files and notes into DIR (no network calls to
                                                the Internet Archive)
  python scraper/snapshot.py archive --tag TAG  ask the Internet Archive to save the source pages, then edit the
                                                release notes of TAG in place; always exits 0

The data comes from data/: the workflow fetches series.json, depauw.json, wabash.json and upcoming.json from the
data branch first (databranch.py); overrides.json is on main. The change check compares a hash of series.json,
depauw.json, wabash.json and overrides.json with the hash the last release recorded in its notes. data/upcoming.json is attached to every release but does not count
as a change, because it changes with every schedule update.

The Wayback Machine part runs after the release exists and is best effort: it has a total time budget and a
per-page timeout, a failure logs a warning, and it never fails the run or touches the release files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCES = ROOT / "scraper" / "sources.json"

TAG_PREFIX = "data-"
HASH_MARK = "<!-- data-hash: "
DATA_FILES = ("series.json", "depauw.json", "wabash.json", "overrides.json", "upcoming.json")
HASHED_FILES = ("series.json", "depauw.json", "wabash.json", "overrides.json")
USER_AGENT = "DePauw-vs-Wabash-fan-site snapshot (https://github.com/jacksonmote/DePauw-vs-Wabash-web)"
SAVE_URL = "https://web.archive.org/save/"
SAVE_TIMEOUT = 180            # seconds to wait on one page (Save Page Now can be slow)
SAVE_PAUSE = 6                # seconds between requests
SAVE_BUDGET = 1800            # seconds for the whole archive step; pages left over are recorded as not saved
ARCHIVE_START = "<!-- archive:start -->"
ARCHIVE_END = "<!-- archive:end -->"
LINK_RE = re.compile(r"https://web\.archive\.org/web/\d+[a-z_]*/(https?://\S+)")
TAG_RE = re.compile(r"data-\d{4}-\d{2}-\d{2}(-\d+)?")


def data_hash(data_dir: Path = DATA) -> str:
    h = hashlib.sha256()
    for name in HASHED_FILES:
        p = Path(data_dir) / name
        h.update(name.encode() + b"\0" + (p.read_bytes() if p.exists() else b"") + b"\0")
    return h.hexdigest()[:20]


def recorded_hash(notes: str) -> str | None:
    m = re.search(re.escape(HASH_MARK) + r"([0-9a-f]+) -->", notes or "")
    return m.group(1) if m else None


def newest_data_release(releases: list) -> str | None:
    """The tag of the newest data-* release, from `gh release list` rows."""
    tags = sorted(r["tagName"] for r in releases if r["tagName"].startswith(TAG_PREFIX))
    return tags[-1] if tags else None


def pick_tag(day: dt.date, existing: set) -> str:
    """data-YYYY-MM-DD, with -2, -3 ... added only when that tag is already taken (a second run that day)."""
    base = f"{TAG_PREFIX}{day.isoformat()}"
    tag, n = base, 1
    while tag in existing:
        n += 1
        tag = f"{base}-{n}"
    return tag


def source_urls(cfg: dict) -> list:
    """Every history page the site reads: DePauw's six, then Wabash's six."""
    d, w = cfg["base_url"].rstrip("/"), cfg["wabash"]["base_url"].rstrip("/")
    return [d + s["path"] for s in cfg["sports"]] + [w + s["wabash_path"] for s in cfg["sports"]]


def archive_section(links: list | None, failed: list | None = None, requested: bool = True) -> str:
    """The notes block between the archive markers. links None means the archive has not run yet."""
    if not requested:
        body = ["Wayback Machine copies: not requested for this snapshot."]
    elif links is None:
        body = ["Wayback Machine copies: being saved"]
    elif links:
        body = ["Wayback Machine copies of the source pages, saved after this snapshot was made:", ""]
        body += [f"- {link}" for link in links]
    else:
        body = ["Wayback Machine copies: none could be saved this time."]
    if failed:
        body += ["", f"Not saved ({len(failed)}): " + ", ".join(failed)]
    return "\n".join([ARCHIVE_START, *body, ARCHIVE_END])


def ties(n: int) -> str:
    return f"{n} tie" if n == 1 else f"{n} ties"


def release_notes(series: dict, data_digest: str, day: dt.date, archive: str | None = None) -> str:
    t = series["totals"]
    lines = [f"Snapshot of the data behind depauwvswabash.com on {day.isoformat()}.", "",
             f"**All head-to-head sports:** DePauw {t['depauw_wins']}, Wabash {t['wabash_wins']}, "
             f"{ties(t['ties'])}, {t['games_played']} games.", ""]
    for s in series["sports"].values():
        r = s["record"]
        unit = (s.get("unit") or ["game", "games"])[1]
        lines.append(f"- {s['name']}: DePauw {r['depauw_wins']}, Wabash {r['wabash_wins']}, {ties(r['ties'])} "
                     f"({s['games_played']} {unit})")
    lines += ["", "Attached: `series.json` (the merged record), `depauw.json` and `wabash.json` (each school's "
              "games as read), `overrides.json` (the rulings), `upcoming.json` (the schedule at that moment) and "
              "`games.csv` (one row per game).", "",
              archive or archive_section(None), "", f"{HASH_MARK}{data_digest} -->"]
    return "\n".join(lines) + "\n"


def replace_archive(notes: str, section: str) -> str:
    """Swap the archive block in release notes for a new one; everything else is left as it was.
    Notes from before the markers existed have their old Internet Archive paragraph replaced instead."""
    pat = re.compile(re.escape(ARCHIVE_START) + r".*?" + re.escape(ARCHIVE_END), re.S)
    if pat.search(notes):
        return pat.sub(lambda _: section, notes, count=1)
    legacy = re.compile(r"(Internet Archive copies of the source pages|The Internet Archive saved none).*?(?=\n*"
                        + re.escape(HASH_MARK) + r"|\Z)", re.S)
    if legacy.search(notes):
        return legacy.sub(lambda _: section, notes, count=1)
    i = notes.find(HASH_MARK)
    if i < 0:
        return notes.rstrip("\n") + "\n\n" + section + "\n"
    return notes[:i] + section + "\n\n" + notes[i:]


def archived_pages(notes: str) -> dict:
    """{source url: archive link} for every Wayback link already in the release notes."""
    return {m.group(1): m.group(0) for m in LINK_RE.finditer(notes or "")}


def wayback_save(urls: list, opener=urllib.request.urlopen, sleep=time.sleep, log=print,
                 clock=time.monotonic, budget: float = SAVE_BUDGET, timeout: float = SAVE_TIMEOUT) -> tuple:
    """Ask the Internet Archive to save each page. Returns (archive links, urls it did not save). Never raises.
    Stops when the total budget is used up; every page not tried is returned as not saved. The timeout is
    urllib's per-socket-operation limit, so the budget is the real bound on the whole step."""
    links, failed = [], []
    start = clock()
    for i, url in enumerate(urls):
        if i:
            sleep(SAVE_PAUSE)
        left = budget - (clock() - start)
        if left <= 0:
            failed += urls[i:]
            log(f"WARN time budget of {budget:.0f}s used up; not saved: {len(urls) - i} pages", file=sys.stderr)
            break
        try:
            req = urllib.request.Request(SAVE_URL + url, headers={"User-Agent": USER_AGENT})
            with opener(req, timeout=min(timeout, left)) as resp:
                link = resp.headers.get("Content-Location") or resp.geturl()
            if link.startswith("/"):
                link = "https://web.archive.org" + link
            if "/web/" not in link:
                raise ValueError(f"no archive link came back ({link})")
            links.append(link)
            log(f"OK   archived {url}")
        except Exception as exc:  # best effort: 429s, timeouts and refusals are expected now and then
            failed.append(url)
            log(f"WARN could not archive {url}: {exc}", file=sys.stderr)
    return links, failed


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def archive_release(tag: str, run=gh, saver=wayback_save, log=print) -> bool:
    """Archive the source pages that have no link in the notes of release `tag` yet, then edit the notes in place:
    new links are added, existing links are never removed, and "not saved" lists only pages still missing.
    Never raises; True if the notes are up to date. The release files and the rest of the notes are not touched."""
    try:
        if not TAG_RE.fullmatch(tag or ""):
            raise ValueError(f"{tag!r} is not a data-YYYY-MM-DD release tag")
        notes = json.loads(run("release", "view", tag, "--json", "body"))["body"]
        urls = source_urls(json.loads(SOURCES.read_text(encoding="utf-8")))
        have = archived_pages(notes)                      # a retry only tries pages with no link yet
        todo = [u for u in urls if u not in have]
        if todo:
            new_links, _ = saver(todo)
            for link in new_links:
                m = LINK_RE.fullmatch(link)
                if m:
                    have.setdefault(m.group(1), link)
        else:
            log(f"OK   release {tag} already has a link for every page; nothing to do")
        ordered = [u for u in urls if u in have] + [u for u in have if u not in urls]   # existing links are never dropped
        links = [have[u] for u in ordered]
        failed = [u for u in urls if u not in have]
        updated = replace_archive(notes, archive_section(links, failed))
        if updated == notes:
            log(f"OK   release {tag} notes already up to date")
            return True
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "notes.md"
            f.write_text(updated, encoding="utf-8", newline="")
            run("release", "edit", tag, "--notes-file", str(f))
        log(f"OK   release {tag} notes updated: {len(links)} saved in all, {len(failed)} not saved")
        return True
    except Exception as exc:  # the archive is a nice extra: whatever goes wrong, the run stays green
        log(f"WARN archiving {tag} did not finish: {exc!r}", file=sys.stderr)
        return False


def changed_since_last_release(run=gh, data_dir: Path = DATA) -> tuple:
    """(changed, reason). A first snapshot always counts as a change."""
    tag = newest_data_release(json.loads(run("release", "list", "--limit", "100", "--json", "tagName")))
    if tag is None:
        return True, "no data-* release yet"
    last = recorded_hash(json.loads(run("release", "view", tag, "--json", "body"))["body"])
    if last is None:
        return True, f"{tag} recorded no data hash"
    now = data_hash(data_dir)
    return now != last, f"{tag} recorded {last}, the data is {now}"


def build(out: Path, run=gh, data_dir: Path = DATA, today: dt.date | None = None, archive: bool = True) -> str:
    """Write the release files. Makes no request to the Internet Archive: that happens after the release."""
    import render                                    # imported here: the check step needs only the standard library

    out.mkdir(parents=True, exist_ok=True)
    series = json.loads((data_dir / "series.json").read_text(encoding="utf-8"))
    for name in DATA_FILES:
        (out / name).write_bytes((data_dir / name).read_bytes())
    (out / "games.csv").write_text(render.render_csv(series), encoding="utf-8", newline="")
    from timeutil import to_eastern
    day = today or to_eastern(dt.datetime.now(dt.timezone.utc))[0].date()
    existing = {r["tagName"] for r in json.loads(run("release", "list", "--limit", "200", "--json", "tagName"))}
    tag = pick_tag(day, existing)
    (out / "NOTES.md").write_text(release_notes(series, data_hash(data_dir), day, archive_section(None, requested=archive)), encoding="utf-8")
    (out / "TAG").write_text(tag + "\n", encoding="utf-8")
    return tag


def _output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--archive", choices=("true", "false"), default="true",
                   help="false: the notes say the Internet Archive copies were not requested")
    a = sub.add_parser("archive")
    a.add_argument("--tag", required=True)
    args = ap.parse_args(argv)
    if args.command == "check":
        changed, why = changed_since_last_release()
        print(f"changed={'true' if changed else 'false'}: {why}")
        _output("changed", "true" if changed else "false")
        return 0
    if args.command == "archive":
        archive_release(args.tag)
        return 0                                          # never fails the run, whatever happened
    print(f"Snapshot tag: {build(args.out, archive=args.archive == 'true')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
