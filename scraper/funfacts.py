"""
The tiger easter egg's facts ("A tiger with something to say" on the home page): validation of the hand-curated
data/fun-facts.json and the helpers render.py needs.

Nothing here is scraped. The file is edited by hand and reviewed through pull requests; render.build validates it
on every build (a bad file fails the render). Every fact is one or two sentences in our own words with at least
one source link, about DePauw, Wabash or both. Keep the two schools balanced: the DePauw and Wabash counts may
differ by at most one ("both" facts count toward neither). No statistic the athletics data already shows.

  validate            refuse missing or inconsistent data (a source with an https URL for every fact, unique ids,
                      non-empty text under MAX_TEXT characters, balanced school counts)
  school_counts       how many facts each school has
  fallback_index      which fact is shown without JavaScript, from the Eastern calendar date (never random)
  SCHOOL_LABELS       the label printed before each fact
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse

MAX_TEXT = 200
MAX_IMBALANCE = 1
SCHOOLS = ("depauw", "wabash", "both")
SCHOOL_LABELS = {"depauw": "DePauw", "wabash": "Wabash", "both": "Both schools"}
_BAD_URL_CHARS = " \t\r\n\"'<>\\"


class FunFactsError(ValueError):
    """Curated fun-fact data is missing or inconsistent."""


def _fail(message):
    raise FunFactsError(message)


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field} must be a non-empty string")


def _url(value, field):
    _text(value, field)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or any(c in value for c in _BAD_URL_CHARS):
        _fail(f"{field} must be an absolute https URL with a host")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read fun facts at {path}: {exc}")
    if not isinstance(value, dict):
        _fail("fun facts must be a JSON object")
    return value


def school_counts(data: dict) -> dict:
    return {school: sum(1 for f in data["facts"] if f["school"] == school) for school in SCHOOLS}


def validate(data: dict) -> dict:
    """Return `data` when it is complete and consistent, else raise FunFactsError naming the first problem."""
    if isinstance(data.get("schema_version"), bool) or data.get("schema_version") != 1:
        _fail("fun facts must use schema_version 1")
    verified = data.get("verified_at")
    _text(verified, "verified_at")
    try:
        if dt.date.fromisoformat(verified).isoformat() != verified:
            raise ValueError
    except ValueError:
        _fail("verified_at must be an ISO date (YYYY-MM-DD)")
    facts = data.get("facts")
    if not isinstance(facts, list) or not facts:
        _fail("facts must be a non-empty list")
    ids = set()
    for fact in facts:
        if not isinstance(fact, dict):
            _fail("each fact must be an object")
        fid = fact.get("id")
        if not isinstance(fid, str) or not fid.strip() or fid in ids:
            _fail(f"invalid or duplicate fact id: {fid!r}")
        if fid != fid.lower() or " " in fid:
            _fail(f"fact id {fid!r} must be lowercase with no spaces")
        ids.add(fid)
        if fact.get("school") not in SCHOOLS:
            _fail(f"fact {fid} has invalid school {fact.get('school')!r}")
        _text(fact.get("text"), f"fact {fid}.text")
        if len(fact["text"]) > MAX_TEXT:
            _fail(f"fact {fid}.text is {len(fact['text'])} characters; the limit is {MAX_TEXT}")
        sources = fact.get("sources")
        if not isinstance(sources, list) or not sources:
            _fail(f"fact {fid}.sources must be a non-empty list of sources")
        for i, source in enumerate(sources):
            if not isinstance(source, dict):
                _fail(f"fact {fid}.sources[{i}] must be an object")
            _text(source.get("label"), f"fact {fid}.sources[{i}].label")
            _url(source.get("url"), f"fact {fid}.sources[{i}].url")
    counts = school_counts(data)
    if abs(counts["depauw"] - counts["wabash"]) > MAX_IMBALANCE:
        _fail(f"the schools are unbalanced: {counts['depauw']} DePauw facts and {counts['wabash']} Wabash facts "
              f"(they may differ by at most {MAX_IMBALANCE})")
    return data


def fallback_index(count: int, today: dt.date) -> int:
    """The fact shown without JavaScript: the date's day number modulo the fact count. Stable for a given date,
    different on the next one, and never random, so a build and its tests are predictable."""
    if count < 1:
        raise FunFactsError("there are no facts to choose from")
    return today.toordinal() % count
