"""Every page's meta description is short enough for a search result and says the same thing in the share tags."""

import datetime as dt
import json
import re
import sys
from html import unescape
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import render  # noqa: E402

NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("desc") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "none.json")
    return out


def meta(page, key):
    m = re.search(rf'<meta (?:name|property)="{re.escape(key)}" content="([^"]*)"', page)
    return unescape(m.group(1)) if m else None


def test_every_description_is_155_characters_or_fewer_and_matches_its_share_tags(site):
    pages = sorted(site.rglob("*.html"))
    assert len(pages) == 7
    for p in pages:
        page = p.read_text(encoding="utf-8")
        d = meta(page, "description")
        assert d and len(d) <= 155, (p.name, len(d or ""))
        if p.name == "404.html":
            continue
        if p.relative_to(site).as_posix() == "bell-heist/index.html":
            assert 'name="robots" content="noindex"' in page
            assert "canonical" not in page and "og:" not in page and "twitter:" not in page
            continue
        assert meta(page, "og:description") == d == meta(page, "twitter:description"), p


def test_the_three_rewritten_descriptions(site):
    alumni = meta((site / "alumni" / "index.html").read_text(encoding="utf-8"), "description")
    assert alumni == ("Notable DePauw and Wabash graduates in ten award and public-service categories, "
                      "plus endowment per student, with a source for every name.")
    total = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))["totals"]["games_played"]
    how = meta((site / "how-it-works" / "athletics" / "index.html").read_text(encoding="utf-8"), "description")
    assert how.startswith(f"How the DePauw vs. Wabash records are built: {total} games in 7 sports")
    manifest = json.loads((site / "site.webmanifest").read_text(encoding="utf-8"))
    assert manifest["description"] == ("DePauw vs. Wabash, side by side: head-to-head athletics records, "
                                       "notable graduates and endowments.")
