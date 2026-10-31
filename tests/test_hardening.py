"""Small input-hardening rules, each pinned both ways: what is now refused, and that everything real still passes
unchanged. Offline: fake responses, the frozen fixture and made-up data; no network, no clock."""
import csv
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alumni  # noqa: E402
import heists  # noqa: E402
import render  # noqa: E402
import scrape  # noqa: E402
import snapshot  # noqa: E402
from test_merged_page import merged_sport  # noqa: E402
from test_render import game  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FROZEN = json.loads((ROOT / "tests" / "fixtures" / "series-frozen-2026-10-30.json").read_text(encoding="utf-8"))


# ---- safe_url ----------------------------------------------------------------------------------------------------
def real_links():
    """Every link the site builds with safe_url: box scores in the frozen data, and the sources' own pages."""
    out = {g["boxscore"] for s in FROZEN["sports"].values() for g in s["games"] if g.get("boxscore")}
    cfg = json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8"))
    for s in FROZEN["sports"].values():
        out |= {s.get("source_url"), s.get("wabash_source_url")}
    for sport in cfg["sports"]:
        for school in (cfg, cfg["wabash"]):
            for key in ("path", "wabash_path", "schedule_path", "wabash_schedule_path"):
                if sport.get(key):
                    out.add(school["base_url"] + sport[key])
    return sorted(u for u in out if u)


def test_every_real_link_passes_through_unchanged():
    links = real_links()
    assert len(links) > 20
    for url in links:
        assert render.safe_url(url) == url, url


@pytest.mark.parametrize("url", [
    "https://evil.example\\@depauwtigers.com/x",         # a browser reads the backslash as a slash: goes to evil.example
    "https://depauwtigers.com\\evil.example/x",
    "https://depauwtigers.com/x y",                       # whitespace
    "https://depauwtigers.com/x\n",                       # trailing newline
    "https://depauwtigers.com/x\t",
    "https://depauwtigers.com/\x00x",                     # control character
    'https://depauwtigers.com/x"onmouseover="alert(1)',   # quotes
    "https://depauwtigers.com/x'y", "https://depauwtigers.com/<x>", "https://depauwtigers.com/x`",
    "https://user:pw@depauwtigers.com/x", "https://depauwtigers.com@evil.example/x",
    "https://depauwtigers.com:8443/x", "https://depauwtigers.com:/x", "https://depauwtigers.com:abc/x",
    "HTTPS://depauwtigers.com/x", "https://DePauwTigers.com/x", "https://SPORTS.wabash.edu/x",
    "http://depauwtigers.com/x", "javascript:alert(1)", "//depauwtigers.com/x", "https://evil.example/x",
    "https://depauwtigers.com.evil.example/x", "", None, 5,
])
def test_a_link_with_anything_unusual_is_dropped(url):
    assert render.safe_url(url) is None


# ---- games.csv ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_a_cell_that_could_run_as_a_formula_gets_a_leading_apostrophe(start):
    assert render.csv_text(start + "SUM(A1)") == "'" + start + "SUM(A1)"


def test_ordinary_cells_are_untouched():
    for value in ("Scheumann Stadium, Muncie, Ind.", "1941", "neutral", "", None, 0, "both", "1891-11-02"):
        assert render.csv_text(value) == ("" if value is None else str(value))


def test_every_free_text_column_in_the_csv_is_guarded():
    g = game("1941-11-15", "L", 19, 27, "away")
    g.update({"source": "=cmd", "site": "\t=cmd", "note": "@x", "season": "-1", "wabash_date": "+1", "date_differs": True})
    g["location"] = "\r=cmd"
    data = {"sports": {"football": merged_sport("football", "=Football", [g])}}
    row = next(csv.DictReader(io.StringIO(render.render_csv(data))))
    for column in ("sport", "season", "location", "site", "note", "source", "wabash_record_date"):
        assert row[column][:1] == "'", (column, row[column])


# ---- fetch -------------------------------------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, url, body=b"<html>ok</html>", history=(), headers=None, encoding="utf-8", status=200):
        self.url, self.history, self.encoding, self.status = url, list(history), encoding, status
        self.headers = headers if headers is not None else {}
        self._chunks = [body[i:i + 65536] for i in range(0, len(body), 65536)] or [b""]
        self.closed = False
        self.read = 0

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            self.read += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


def patch_get(monkeypatch, response):
    import requests
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return response
    monkeypatch.setattr(requests, "get", fake_get)
    return seen


URL = "https://depauwtigers.com/sports/football/opponent-history/wabash/215"


def test_a_normal_page_comes_back_as_text_and_is_streamed(monkeypatch):
    resp = FakeResponse(URL, "<p>café ’</p>".encode("utf-8"))
    seen = patch_get(monkeypatch, resp)
    assert scrape.fetch(URL) == "<p>café ’</p>"
    assert seen["stream"] is True and seen["timeout"] == 30 and "User-Agent" in seen["headers"] and resp.closed


def test_a_page_with_no_declared_encoding_is_read_as_utf8(monkeypatch):
    patch_get(monkeypatch, FakeResponse(URL, "’".encode("utf-8"), encoding=None))
    assert scrape.fetch(URL) == "’"


def test_the_www_form_of_the_same_site_is_fine(monkeypatch):
    patch_get(monkeypatch, FakeResponse("https://www.depauwtigers.com/x", history=[FakeResponse(URL)]))
    assert scrape.fetch(URL) == "<html>ok</html>"


@pytest.mark.parametrize("final, hops", [
    ("https://evil.example/x", []),                                               # ends on another site
    ("http://depauwtigers.com/x", []),                                            # drops to plain http
    (URL, ["https://evil.example/hop"]),                                          # passes through another site and comes back
    ("https://depauwtigers.com.evil.example/x", []),
])
def test_a_redirect_to_another_site_or_to_http_is_refused(monkeypatch, final, hops):
    patch_get(monkeypatch, FakeResponse(final, history=[FakeResponse(h) for h in hops]))
    with pytest.raises(ValueError, match="redirected"):
        scrape.fetch(URL)


def test_a_page_that_declares_too_much_is_refused_before_reading(monkeypatch):
    resp = FakeResponse(URL, headers={"Content-Length": str(scrape.MAX_PAGE_BYTES + 1)})
    patch_get(monkeypatch, resp)
    with pytest.raises(ValueError, match="limit"):
        scrape.fetch(URL)
    assert resp.read == 0 and resp.closed


def test_a_page_that_turns_out_too_big_is_cut_off_while_reading(monkeypatch):
    resp = FakeResponse(URL, b"x" * (scrape.MAX_PAGE_BYTES + 1))                  # no Content-Length header at all
    patch_get(monkeypatch, resp)
    with pytest.raises(ValueError, match="limit"):
        scrape.fetch(URL)
    assert resp.closed


def test_a_page_right_at_the_limit_is_accepted(monkeypatch):
    patch_get(monkeypatch, FakeResponse(URL, b"x" * scrape.MAX_PAGE_BYTES))
    assert len(scrape.fetch(URL)) == scrape.MAX_PAGE_BYTES


def test_the_limit_is_far_above_the_biggest_real_page():
    assert scrape.MAX_PAGE_BYTES >= 5 * 1_100_000                                  # men's basketball history is about 1 MB


def test_an_error_status_still_raises_and_closes(monkeypatch):
    resp = FakeResponse(URL, status=503)
    patch_get(monkeypatch, resp)
    with pytest.raises(RuntimeError):
        scrape.fetch(URL)
    assert resp.closed


# ---- curated data links ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("module", [alumni, heists])
def test_curated_links_must_be_https(module):
    module._url("https://example.org/a", "x")
    for bad in ("http://example.org/a", "ftp://example.org/a", "javascript:alert(1)", "https://", "example.org"):
        with pytest.raises(Exception, match="https URL"):
            module._url(bad, "x")


def test_the_real_curated_data_has_no_plain_http_link():
    for name in ("alumni.json", "endowment.json", "bell-heists.json"):
        assert "http://" not in (ROOT / "data" / name).read_text(encoding="utf-8").lower(), name


# ---- Wayback links -----------------------------------------------------------------------------------------------
def wayback(link):
    class Resp:
        headers = {"Content-Location": link}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "https://web.archive.org/save/x"
    return snapshot.wayback_save(["https://a/1"], opener=lambda req, timeout: Resp(), sleep=lambda s: None, log=lambda *a, **k: None)


def test_only_a_web_archive_org_web_link_is_recorded():
    assert wayback("/web/20270102030405/https://a/1") == (["https://web.archive.org/web/20270102030405/https://a/1"], [])
    assert wayback("https://web.archive.org/web/2027/https://a/1")[0] == ["https://web.archive.org/web/2027/https://a/1"]
    for bad in ("https://evil.example/web/2027/x", "http://web.archive.org/web/2027/x", "https://web.archive.org/save/x",
                "/save/x", "https://a/1?/web/", ""):
        assert wayback(bad) == ([], ["https://a/1"]), bad
