"""Visible text uses the curly apostrophe (’), never the straight one ('), on every page.

Only what a reader sees counts: text, the <title>, meta descriptions and share-card text, and alt, aria-label and
title attributes. Links, ids, classes and other attributes, the data files, sources.json keys and the values in
games.csv keep whatever the source used."""

import collections
import datetime as dt
import glob
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import render  # noqa: E402

NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
READ_ATTRS = ("alt", "aria-label", "title", "placeholder")


class Visible(HTMLParser):
    """Collects every straight apostrophe that a reader could see or hear."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hits, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        d = dict(attrs)
        for name, value in attrs:
            if not value or "'" not in value:
                continue
            shown = (name in READ_ATTRS
                     or (tag == "meta" and name == "content"
                         and (d.get("name") in ("description", "twitter:title", "twitter:description", "twitter:image:alt")
                              or (d.get("property") or "").startswith("og:") and d.get("property") != "og:url")))
            if shown:
                self.hits.append((f"<{tag} {name}>", value[:100]))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and "'" in data:
            self.hits.append(("text", data.strip()[:100]))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("apos") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "none.json")
    return out


def straight(page_text):
    parser = Visible()
    parser.feed(page_text)
    return parser.hits


def test_no_page_has_a_straight_apostrophe_in_visible_text(site):
    pages = sorted(site.rglob("*.html"))
    assert len(pages) == 7
    found = {str(p.relative_to(site)): straight(p.read_text(encoding="utf-8")) for p in pages}
    assert {k: v for k, v in found.items() if v} == {}


def test_the_scanner_does_see_a_straight_apostrophe_in_text_titles_and_alt_text_but_not_in_hrefs_or_ids():
    page = ('<html><head><title>Men\'s</title><meta name="description" content="Wabash\'s">'
            '<meta property="og:image:alt" content="DePauw\'s"></head><body><a href="/x?sport=Men\'s" id="a\'b">ok</a>'
            "<p>Men's</p><img alt=\"Men's\" src=\"x.png\"><script>var a = 'x';</script></body></html>")
    kinds = [kind for kind, _ in straight(page)]
    assert kinds == ["text", "<meta content>", "<meta content>", "text", "<img alt>"]


def test_sport_names_read_with_the_curly_apostrophe_on_the_page_and_stay_straight_in_the_data(site):
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "Men’s Basketball" in page and "Men’s Swimming and Diving" in page
    data = json.loads((site / "data" / "series.json").read_text(encoding="utf-8"))
    assert data["sports"]["mens-basketball"]["name"] == "Men's Basketball"        # the data keeps the source's spelling
    assert "Men's Basketball" in (site / "games.csv").read_text(encoding="utf-8")       # games.csv keeps the data's spelling
    sources = json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8"))
    assert any("'" in s["name"] for s in sources["sports"])                       # sources.json is unchanged


def test_the_issue_forms_use_the_curly_apostrophe_and_the_prefilled_sport_matches_the_page(site):
    """Both forms are read on GitHub, so their wording follows the site. The sport box is a free-text input (no
    dropdown option has to equal it), and the report links prefill it with the same curly name the card shows."""
    import yaml

    def strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for v in node.values():
                yield from strings(v)
        elif isinstance(node, list):
            for v in node:
                yield from strings(v)

    for name in ("data-correction.yml", "alumni-correction.yml"):
        form = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / name).read_text(encoding="utf-8"))
        assert [t for t in strings(form) if "'" in t] == [], name
    form = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "data-correction.yml").read_text(encoding="utf-8"))
    assert next(item for item in form["body"] if item.get("id") == "sport")["type"] == "input"
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "sport=Men%E2%80%99s+Basketball" in page or "sport=Men%E2%80%99s%20Basketball" in page
