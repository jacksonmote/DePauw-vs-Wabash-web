"""
The review hold: a school's edit to an old result waits for a person before it reaches the site.

A school's history page is first checked against its own summary (scrape.py, unchanged). Then each
school's newly parsed games are compared with that school's saved copy (data/depauw.json or
data/wabash.json). A game is "historical" when its season is before the sport's current season, and the
current season is the newest season present in either school's saved or newly parsed games for that sport
(not a calendar rule, so a score fix in the week after a season ends still goes straight through).

A historical change is an edited score, result, date, site or note, a newly added old game, or a removed
old game. Those are held: the saved version stays, the change is recorded in pending-changes.json (on the data branch),
and a GitHub issue lists it (review_issue.py). Current-season changes are applied as always.

  approve  run the workflow with apply_reviewed_changes: a change that still matches a pending entry
           exactly is applied and removed from pending; anything different is held as a new entry.
  reject   add a "keep_previous" ruling to data/overrides.json (see KEEP_PREVIOUS_HELP): the change is
           ignored from then on.

Standard library only (the issue job installs nothing). Nothing here reads the clock: `now` is passed in.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import merge
import places

PENDING_SCHEMA = 1
LABEL = "data-review"
FIELDS = ("date", "season", "location", "site", "result", "depauw", "wabash", "note")

KEEP_PREVIOUS_HELP = (
    'Add to data/overrides.json under "sports" -> <sport key> -> "keep_previous" a ruling like '
    '{"school": "wabash", "old": {"date": "1955-10-08", "score": "14-7"}, '
    '"new": {"date": "1955-10-08", "score": "14-17"}, "reason": "why"}. '
    '"old" is null for an added game and "new" is null for a removed one.'
)


def _clean(v):
    return re.sub(r"\s+", " ", v).strip() if isinstance(v, str) else v


def _plain(field: str, v):
    """A value with only its styling removed: spacing, capitalization and, for a place, the state
    abbreviation style ("IN" and "Ind." are the same place). A restyle is not a change to the game."""
    v = _clean(v)
    if not isinstance(v, str):
        return v
    if field in ("location", "site"):
        v = places.ap_place(v)
    return v.casefold()


def norm(g: dict) -> dict:
    """The fields that count as the game, compared with styling removed (so only a real edit differs)."""
    return {k: _plain(k, g.get(k)) for k in FIELDS} | {"boxscore": _clean(g.get("boxscore"))}


def _frozen(g: dict) -> str:
    return json.dumps(norm(g), sort_keys=True)


def score_text(g: dict) -> str:
    """DePauw-Wabash, as a ruling writes it. A game whose school lists no score reads "no score"."""
    return "no score" if g.get("score_known") is False else f"{g['depauw']}-{g['wabash']}"


def side(g: dict | None) -> dict | None:
    return None if g is None else {"date": g["date"], "score": score_text(g)}


def current_season(*game_lists: list) -> int | None:
    """The newest season (the year it ends in) across every list given."""
    keys = [merge.season_key(g) for games in game_lists for g in games or []]
    return max(keys) if keys else None


def _pair_changes(removed: list, added: list) -> list:
    """Pair each removed old version with the added version it was most likely edited into: the same
    date first, then the same season and score. What is left over is a pure removal or addition."""
    out, removed, added = [], list(removed), list(added)
    for pick in (lambda a, b: a["date"] == b["date"],
                 lambda a, b: merge.season_key(a) == merge.season_key(b) and score_text(a) == score_text(b)):
        for r in list(removed):
            m = next((a for a in added if pick(r, a)), None)
            if m is not None:
                out.append((r, m))
                removed.remove(r)
                added.remove(m)
    return out + [(r, None) for r in removed] + [(None, a) for a in added]


def diff(old: list, new: list, current: int | None) -> list:
    """Every historical difference between a school's saved games and its new ones, as
    [{kind: edited|added|removed, old, new}]. Games are compared whole, ignoring styling (see _plain)."""
    left = {}
    for g in old:
        left.setdefault(_frozen(g), []).append(g)
    added = []
    for g in new:
        bucket = left.get(_frozen(g))
        if bucket:
            bucket.pop()
        else:
            added.append(g)
    removed = [g for bucket in left.values() for g in bucket]
    changes = []
    for o, n in _pair_changes(removed, added):
        historical = any(g is not None and current is not None and merge.season_key(g) < current for g in (o, n))
        if historical:
            changes.append({"kind": "edited" if o and n else "added" if n else "removed", "old": o, "new": n})
    return changes


def change_id(school: str, sport: str, c: dict) -> str:
    raw = "|".join([school, sport, c["kind"], _frozen(c["old"]) if c["old"] else "-",
                    _frozen(c["new"]) if c["new"] else "-"])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def entry(school: str, sport: str, c: dict, url: str, first_seen: str, touches_ruling: bool) -> dict:
    e = {"id": change_id(school, sport, c), "school": school, "sport": sport, "kind": c["kind"],
         "old": c["old"], "new": c["new"], "source_url": url, "first_seen": first_seen}
    if touches_ruling:
        e["touches_ruling"] = True
    return e


# --------------------------------------------------------------------------- #
# Rulings that reject a change
# --------------------------------------------------------------------------- #

def load_keep_previous(path: Path) -> dict:
    """{sport key: [ruling, ...]} from data/overrides.json (a missing file or section is none). Raises
    merge.MergeError on a malformed ruling, because a typo must not silently turn a rejection off."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}                                # load_overrides reports the bad file
    out = {}
    for key, rules in (data.get("sports") or {}).items():
        out[key] = [_keep_rule(path.name, key, r) for r in (rules or {}).get("keep_previous", [])]
    return out


def _keep_rule(name: str, key: str, r) -> dict:
    def one(v):
        if v is None:
            return None
        try:
            return {"date": v["date"], "score": v["score"]}
        except (KeyError, TypeError):
            raise merge.MergeError(f'{name}: {key} keep_previous: "old" and "new" are null or have "date" '
                                   f'and "score": {r!r}') from None
    if (not isinstance(r, dict) or r.get("school") not in ("depauw", "wabash") or "old" not in r
            or "new" not in r or (r["old"] is None and r["new"] is None)):
        raise merge.MergeError(f'{name}: {key} keep_previous needs "school" ("depauw" or "wabash"), "old" and '
                               f'"new" (either may be null, not both): {r!r}')
    return {"school": r["school"], "old": one(r["old"]), "new": one(r["new"]), "reason": r.get("reason", "")}


def rejected(school: str, c: dict, rules: list) -> dict | None:
    """The keep_previous ruling that covers this change, if any."""
    for r in rules:
        if r["school"] == school and r["old"] == side(c["old"]) and r["new"] == side(c["new"]):
            return r
    return None


def touches_ruling(c: dict, school: str, rules: dict | None) -> bool:
    """Does the change edit a row that a same_game, different_games or session_of ruling names? Approving it
    would make that ruling stale, and the sport's merge then fails loudly until the ruling is fixed."""
    named = set()
    for kind in ("same_game", "different_games"):
        for r in (rules or {}).get(kind, []):
            named.add((r[school]["date"], r[school]["score"]))
    for r in (rules or {}).get("session_of", []):
        if r["school"] == school:
            named.update((x["date"], x["score"]) for x in [r["meet"], *r["sessions"]])
    return any(g is not None and (g["date"], (g["depauw"], g["wabash"])) in named for g in (c["old"], c["new"]))


# --------------------------------------------------------------------------- #
# Holding changes
# --------------------------------------------------------------------------- #

def apply_holds(old: list, new: list, held: list) -> list:
    """The school's games with every held change put back as it was: an edited game keeps its old
    version, an added game is left out, a removed game is kept."""
    drop = {}
    for c in held:
        if c["new"] is not None:
            drop.setdefault(_frozen(c["new"]), []).append(c)
    result = []
    for g in new:
        bucket = drop.get(_frozen(g))
        if bucket:
            c = bucket.pop()
            if c["old"] is not None:
                result.append(c["old"])
            continue
        result.append(g)
    for c in held:
        if c["old"] is not None and c["new"] is None:
            i = next((k for k, g in enumerate(old) if g is c["old"]), None)
            before = old[i - 1] if i else None
            at = next((k + 1 for k, g in enumerate(result) if before is not None and _frozen(g) == _frozen(before)), 0)
            result.insert(at, c["old"])
    return result


def settle(school: str, sport: str, old: list, new: list, current: int | None, url: str, *, pending: list,
           rules: list, sport_rules: dict | None, apply_reviewed: bool, now: str) -> tuple:
    """Decide one school's one sport. Returns (games to keep, entries now pending for it, notes, the
    keep_previous rulings that matched). `pending` is the whole file's entries; only this school and
    sport's are looked at."""
    known = {e["id"]: e for e in pending if e["school"] == school and e["sport"] == sport}
    held, entries, notes, used = [], [], [], []
    for c in diff(old, new, current):
        rule = rejected(school, c, rules)
        if rule:
            used.append(rule)
            held.append(c)                                # a rejected change is kept out and not raised
            notes.append(f"keep_previous ruling: ignoring a change to {_describe(c)}")
            continue
        cid = change_id(school, sport, c)
        if apply_reviewed and cid in known:
            notes.append(f"applied reviewed change: {_describe(c)}")
            continue                                      # approved: let it through
        held.append(c)
        entries.append(known.get(cid) or entry(school, sport, c, url, now, touches_ruling(c, school, sport_rules)))
    return apply_holds(old, new, held), entries, notes, used


def _describe(c: dict) -> str:
    def t(g):
        return "-" if g is None else f"{g['date']} {score_text(g)}"
    return f"{c['kind']}: {t(c['old'])} -> {t(c['new'])}"


# --------------------------------------------------------------------------- #
# The pending file
# --------------------------------------------------------------------------- #

def load_pending(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("changes", []))


def pending_doc(entries: list) -> dict:
    order = sorted(entries, key=lambda e: (e["sport"], e["school"], (e["old"] or e["new"])["date"], e["id"]))
    return {"schema_version": PENDING_SCHEMA, "changes": order}


# --------------------------------------------------------------------------- #
# The issue text
# --------------------------------------------------------------------------- #

ISSUE_TITLE = "Review changes to old games"
HASH_MARK = "<!-- data-review-hash: "
BODY_LIMIT = 60000                      # GitHub rejects an issue body over 65,536 characters
PER_SPORT_CAPS = (25, 10, 5, 2, 0)      # entries shown per sport, tried in turn until the body fits
SCHOOLS = {"depauw": "DePauw", "wabash": "Wabash"}
FIELD_WORDS = {"depauw": "DePauw's score", "wabash": "Wabash's score", "site": "location"}
APPROVE_HELP = (
    'Open the repository on GitHub, then Actions, "Update data and deploy site", Run workflow, tick '
    '"Apply the changes to old games waiting for review", and run it.')


def _score_words(g: dict) -> str:
    return "no score listed" if g.get("score_known") is False else f"DePauw {g['depauw']}-{g['wabash']}"


def _game_text(g: dict | None) -> str:
    if g is None:
        return "(not listed)"
    place = " · ".join(x for x in (g.get("location"), g.get("site")) if x)
    return f"{g['date']} (season {g['season']}) {_score_words(g)} ({g['result']})" \
           + (f" · {place}" if place else "") + (f" · {g['note']}" if g.get("note") else "")


def _changed_fields(o: dict | None, n: dict | None) -> str:
    if o is None or n is None:
        return ""
    names = [FIELD_WORDS.get(k, k) for k in FIELDS if _plain(k, o.get(k)) != _plain(k, n.get(k))]
    return f" (changed: {', '.join(names)})" if names else ""


def _entry_lines(e: dict) -> list:
    snippet = json.dumps({"school": e["school"], "old": side(e["old"]), "new": side(e["new"]),
                          "reason": "why this change is wrong"})
    lines = [f"- **{SCHOOLS[e['school']]}, {e['kind']}**{_changed_fields(e['old'], e['new'])} · first seen "
             f"{e['first_seen'][:10]} · [source page]({e['source_url']})",
             f"  - was: {_game_text(e['old'])}",
             f"  - now: {_game_text(e['new'])}"]
    if e.get("touches_ruling"):
        lines.append("  - ⚠ A ruling in `data/overrides.json` names this row. Approving the change makes that "
                     "ruling stale, so the sport's merge fails until the ruling is fixed, and `series.json` "
                     "stays on its last good merge meanwhile.")
    lines.append(f"  - to reject, add to this sport's `keep_previous` list: `{snippet}`")
    return lines


def render_body(pending: list, repo_url: str = "") -> str:
    """The issue text for the pending changes, capped so it always fits in an issue."""
    for cap in PER_SPORT_CAPS:
        body = _body(pending, cap)
        if len(body) <= BODY_LIMIT:
            break
    h = hashlib.sha256(body.encode()).hexdigest()[:16]
    return f"{body}\n{HASH_MARK}{h} -->\n"


def body_hash(body: str) -> str | None:
    m = re.search(re.escape(HASH_MARK) + r"([0-9a-f]+) -->", body or "")
    return m.group(1) if m else None


def _body(pending: list, cap: int) -> str:
    out = [f"A school's history page now lists {len(pending)} change(s) to games from past seasons. They are "
           "**held**: the site still shows the old version of each until you approve it.", "",
           f"**Approve all:** {APPROVE_HELP}", "",
           "**Reject one:** add the `keep_previous` ruling shown under it to `data/overrides.json` "
           "(under `sports` → the sport's key); the change is then ignored from then on and stops being "
           "raised. The full list is in `data/pending-changes.json` on the `data` branch.", ""]
    sports = []
    for e in pending:
        if e["sport"] not in sports:
            sports.append(e["sport"])
    for sport in sports:
        mine = [e for e in pending if e["sport"] == sport]
        out.append(f"## {sport} ({len(mine)})")
        for e in mine[:cap]:
            out.extend(_entry_lines(e))
        if len(mine) > cap:
            out.append(f"- …and {len(mine) - cap} more, see `data/pending-changes.json` on the `data` branch")
        out.append("")
    return "\n".join(out)
