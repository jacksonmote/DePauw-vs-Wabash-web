"""The privacy policy (/privacy/) says the site collects nothing. These tests keep that true: no cookies or
storage, no network request from the one script, no script on any other page, no resource from another origin,
no personal data in an issue link, and a working Privacy link in every footer. Adding any of those means
changing scraper/privacy.py and these tests in the same pull request.

No clock, no network, no outside programs: the script is checked from its text."""

import datetime as dt
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import heists  # noqa: E402
import privacy  # noqa: E402
import render  # noqa: E402

NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
BASE_CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; manifest-src 'self'; "
            "base-uri 'none'; form-action 'none'; object-src 'none'")
SCRIPT_PAGES = {"index.html": "easter-eggs.js", "alumni/index.html": "../easter-eggs.js"}
FORBIDDEN_IN_SCRIPT = ("fetch", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "document.cookie",
                       "localStorage", "sessionStorage", "indexedDB", "caches", "import(", "importScripts",
                       "new Image", "navigator.", "location")
ISSUE_KEYS = {"template", "title", "sport", "category", "area"}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("privacy") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "none.json")
    return out


def pages(site):
    return sorted(site.rglob("*.html"))


def soup(path):
    return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")


def resolve(site, page, href):
    """Where a relative or root-relative link on `page` points, as a path inside the site folder."""
    rel = "/" + page.relative_to(site).as_posix()
    base = PurePosixPath(rel).parent
    target = PurePosixPath(href) if href.startswith("/") else base / href
    parts = []
    for part in target.parts:
        if part == "..":
            parts.pop()
        elif part not in ("/", "."):
            parts.append(part)
    path = site.joinpath(*parts)
    return path / "index.html" if path.is_dir() or href.endswith("/") else path


# ---- the page itself ------------------------------------------------------------------------------------

def test_the_privacy_page_renders_with_its_title_headings_and_a_fixed_date(site):
    s = soup(site / "privacy" / "index.html")
    assert s.title.string == "Privacy | DePauw vs. Wabash"
    assert [h.get_text() for h in s.find_all("h2")] == ["Privacy policy"]
    assert s.find("link", rel="canonical")["href"] == "https://depauwvswabash.com/privacy/"
    assert s.find("meta", property="og:image")["content"].startswith("https://depauwvswabash.com/og-image.png")
    assert not s.find("meta", attrs={"name": "robots"})
    assert "Last updated Nov 1, 2026" in s.find("p", class_="updated").get_text()
    assert s.find("time")["datetime"] == privacy.UPDATED
    assert not any(a.has_attr("aria-current") for a in s.find("nav").find_all("a"))     # no nav item marked current


def test_the_last_updated_date_comes_from_the_constant_not_the_clock(tmp_path):
    for now in (dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc), dt.datetime(2027, 6, 1, tzinfo=dt.timezone.utc)):
        out = tmp_path / now.date().isoformat()
        render.build(ROOT / "data" / "series.json", out, now=now, upcoming_path=out / "none.json")
        assert f'datetime="{privacy.UPDATED}">{privacy.updated_text()}</time>' in (out / "privacy" / "index.html").read_text(encoding="utf-8")
        entry = re.search(r"<loc>https://depauwvswabash.com/privacy/</loc><lastmod>([^<]+)</lastmod>",
                          (out / "sitemap.xml").read_text(encoding="utf-8"))
        assert entry.group(1) == privacy.UPDATED


def test_the_page_is_in_the_sitemap(site):
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = {u.find("s:loc", ns).text: u.find("s:lastmod", ns).text
            for u in ET.parse(site / "sitemap.xml").getroot().findall("s:url", ns)}
    assert urls["https://depauwvswabash.com/privacy/"] == privacy.UPDATED


def test_the_page_has_the_base_csp_and_no_script(site):
    source = (site / "privacy" / "index.html").read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', source).group(1)
    assert csp.replace("&#x27;", "'") == BASE_CSP
    assert soup(site / "privacy" / "index.html").find_all("script") == []


def test_the_policy_makes_the_promises_the_other_tests_check(site):
    text = soup(site / "privacy" / "index.html").get_text(" ", strip=True)
    assert "This site doesn’t collect, store or sell any personal information. It sets no cookies and uses no analytics, ads or trackers." in text
    assert "It stores nothing and sends nothing anywhere." in text
    for law in ("GDPR", "CCPA", "COPPA", "HIPAA", "FERPA"):
        assert law not in text                     # no claim of compliance with any specific law
    assert "describes how the site works" not in text and "legal advice" not in text and "Children" not in text
    assert "unofficial fan site, not affiliated with DePauw University or Wabash College." in text


# ---- the footer link ------------------------------------------------------------------------------------

def test_every_footer_has_a_privacy_link_that_resolves_to_the_page_and_the_page_does_not_link_to_itself(site):
    target = site / "privacy" / "index.html"
    assert target.exists() and len(pages(site)) == 9
    for page in pages(site):
        footer = soup(page).find("footer", class_="site-footer")
        links = [a for a in footer.find_all("a") if a.get_text() == "Privacy"]
        if page == target:
            assert links == []
            span = [x for x in footer.find_all("span") if x.get_text() == "Privacy"]
            assert len(span) == 1 and span[0]["aria-current"] == "page"
            continue
        assert len(links) == 1, page
        assert resolve(site, page, links[0]["href"]) == target, (page, links[0]["href"])
    own = [a for a in soup(target).find_all("a", href=True) if not urlparse(a["href"]).scheme
           and resolve(site, target, a["href"]) == target]
    assert own == []


# ---- scripts, storage, other origins --------------------------------------------------------------------

def test_only_the_home_and_alumni_pages_have_a_script_and_it_is_exactly_the_easter_egg_file(site):
    for page in pages(site):
        name = page.relative_to(site).as_posix()
        tags = soup(page).find_all("script")
        if name in SCRIPT_PAGES:
            assert [t.attrs for t in tags] == [{"src": SCRIPT_PAGES[name], "defer": ""}] and tags[0].string is None
            assert resolve(site, page, SCRIPT_PAGES[name]) == site / "easter-eggs.js"
        else:
            assert tags == [], name


def test_the_csp_is_the_base_policy_everywhere_plus_script_src_self_on_the_two_script_pages(site):
    for page in pages(site):
        name = page.relative_to(site).as_posix()
        csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"',
                        page.read_text(encoding="utf-8")).group(1).replace("&#x27;", "'")
        assert csp == BASE_CSP + ("; script-src 'self'" if name in SCRIPT_PAGES else ""), name


def test_the_script_has_no_network_cookie_or_storage_code():
    text = (ROOT / "static" / "easter-eggs.js").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
    for word in FORBIDDEN_IN_SCRIPT:
        assert word not in code, word
    assert not re.search(r"https?:|//\w", code)           # no URL at all
    assert not re.search(r"\.(src|href|action)\s*=|setAttribute\(\s*['\"](src|href)", code)


def test_no_page_loads_anything_from_another_origin(site):
    own = "https://depauwvswabash.com"

    def external(value):
        value = value.strip()
        return bool(re.match(r"(?i)(https?:)?//|data:|javascript:", value)) and not value.startswith(own + "/")

    for page in pages(site):
        s = soup(page)
        for tag in s.find_all(["img", "script", "source", "iframe", "embed", "object", "video", "audio", "track", "input", "form"]):
            for attr in ("src", "srcset", "data", "action", "poster"):
                for part in re.split(r",\s*", tag.get(attr, "")) if tag.get(attr) else []:
                    assert not external(part.split()[0] if part.split() else ""), (page, tag)
        for link in s.find_all("link"):
            rels = {r.lower() for r in link.get("rel", [])}
            if rels & {"stylesheet", "preload", "prefetch", "preconnect", "dns-prefetch", "icon", "apple-touch-icon",
                       "manifest", "modulepreload", "prerender"}:
                assert not external(link.get("href", "")), (page, link)
        for meta in s.find_all("meta", attrs={"http-equiv": True}):
            assert meta["http-equiv"].lower() != "refresh", page
        assert not s.find_all("style") and not s.find_all(style=True)
    css = (site / "styles.css").read_text(encoding="utf-8")
    assert [u.strip("'\" ") for u in re.findall(r"url\(([^)]*)\)", css)] == [render.WEB_FONT]
    assert "@import" not in css
    manifest = (site / "site.webmanifest").read_text(encoding="utf-8")
    assert "http" not in manifest


def test_no_form_field_or_phone_link_exists_and_mailto_only_on_the_privacy_page(site):
    for page in pages(site):
        s = soup(page)
        assert not s.find_all(["form", "input", "textarea", "select"]), page
        assert not [a for a in s.find_all("a", href=True) if a["href"].lower().startswith("tel:")], page
        assert [a["href"] for a in s.find_all("a", href=True) if a["href"].lower().startswith("mailto:")] == (
            [] if page.name != "index.html" or page.parent.name != "privacy" else ["mailto:" + privacy.PRIVACY_EMAIL] * 2), page


# ---- issue links ----------------------------------------------------------------------------------------

def test_every_github_issue_link_carries_only_the_template_and_the_page_category_or_sport(site):
    series = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))
    alumni = json.loads((ROOT / "data" / "alumni.json").read_text(encoding="utf-8"))
    sports = {render.curly(s["name"]) for s in series["sports"].values()} | {heists.CORRECTION_SPORT}
    categories = {render.curly(c["name"]) for c in alumni["categories"]} | {"Endowment"}
    areas = {render.AREA_ATHLETICS, render.AREA_ALUMNI, render.AREA_SITE}
    templates = {"data-correction.yml", "alumni-correction.yml", "enhancement.yml"}
    seen = 0
    for page in pages(site):
        for a in soup(page).find_all("a", href=True):
            if not a["href"].startswith(render.REPO_URL + "/issues/new"):
                continue
            seen += 1
            query = parse_qs(urlparse(a["href"]).query, keep_blank_values=True)
            assert set(query) <= ISSUE_KEYS and query["template"][0] in templates, a["href"]
            assert all(len(v) == 1 for v in query.values())
            if "sport" in query:
                assert query["sport"][0] in sports, a["href"]
                assert query["title"][0].startswith("Data correction: ")
            if "category" in query:
                assert query["category"][0] in categories, a["href"]
                assert query["title"][0].startswith("Alumni correction: ")
            if "area" in query:
                assert query["area"][0] in areas, a["href"]
            for values in query.values():
                assert "@" not in values[0] and not re.search(r"\d{3}[-. ]?\d{3,4}", values[0]), a["href"]
    assert seen > 20


def test_the_hosting_section_names_no_network_and_links_githubs_own_statements(site):
    s = soup(site / "privacy" / "index.html")
    text = s.get_text(" ", strip=True)
    for word in ("Fastly", "content-delivery", "CDN", "Cloudflare"):
        assert word not in text
    hrefs = {a["href"] for a in s.find_all("a", href=True)}
    assert {privacy.GITHUB_PAGES_DATA, privacy.GITHUB_PRIVACY, privacy.GITHUB_SUBPROCESSORS} <= hrefs
    assert privacy.GITHUB_PAGES_DATA.startswith("https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages")
    assert "its address points directly at GitHub." in text
    assert "don’t see or receive those logs" in text


def test_the_privacy_email_is_on_the_privacy_page_only_as_one_mailto_link_and_nowhere_else_on_the_site(site):
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
    target = site / "privacy" / "index.html"
    s = soup(target)
    mailto = [a for a in s.find_all("a", href=True) if a["href"].lower().startswith("mailto:")]
    assert mailto and {a["href"] for a in mailto} == {"mailto:" + privacy.PRIVACY_EMAIL}
    assert all(a.get_text() == privacy.PRIVACY_EMAIL for a in mailto)
    assert set(email.findall(target.read_text(encoding="utf-8"))) == {privacy.PRIVACY_EMAIL}
    for path in site.rglob("*"):
        if path.is_file() and path != target and path.suffix in {".html", ".txt", ".json", ".csv", ".xml", ".webmanifest", ".css", ".js", ".svg"}:
            assert not email.search(path.read_text(encoding="utf-8")), path
            assert "mailto:" not in path.read_text(encoding="utf-8"), path
    security = (site / ".well-known" / "security.txt").read_text(encoding="utf-8")
    assert "@" not in security and "mailto" not in security.lower()


def test_the_alumni_definition_of_public_information_is_the_same_on_both_pages(site):
    for name in ("privacy/index.html", "how-it-works/alumni/index.html"):
        text = soup(site / name).get_text(" ", strip=True)
        assert privacy.PUBLIC_INFO in text, name
    how = soup(site / "how-it-works" / "alumni" / "index.html")
    link = [a for a in how.find_all("a") if a.get_text() == "privacy page"]
    assert len(link) == 1 and resolve(site, site / "how-it-works" / "alumni" / "index.html", link[0]["href"]) == site / "privacy" / "index.html"
    text = soup(site / "privacy" / "index.html").get_text(" ", strip=True)
    assert "Alumni entries are limited to public roles and public sources" in text
    assert "A short, general request is enough, with no personal details needed, and we’ll consider it." in text


def test_the_policy_text_file_exists_and_both_changes_links_point_at_it(site):
    assert (ROOT / privacy.SOURCE_PATH).is_file()
    assert Path(privacy.__file__).resolve() == (ROOT / privacy.SOURCE_PATH).resolve()
    assert f'UPDATED = "{privacy.UPDATED}"' in (ROOT / privacy.SOURCE_PATH).read_text(encoding="utf-8")
    history, source = privacy.source_urls(render.REPO_URL)
    assert history == f"{render.REPO_URL}/commits/main/{privacy.SOURCE_PATH}"
    assert source == f"{render.REPO_URL}/blob/main/{privacy.SOURCE_PATH}"
    hrefs = {a.get_text(): a["href"] for a in soup(site / "privacy" / "index.html").find_all("a", href=True)}
    assert hrefs["changes to this page"] == history and hrefs["the page’s source"] == source


def test_the_contact_section_is_only_the_privacy_contact(site):
    s = soup(site / "privacy" / "index.html")
    contact = s.find("h3", string="Contact")
    paragraphs = []
    for sibling in contact.find_next_siblings():
        paragraphs.append(sibling)
    text = " ".join(p.get_text() for p in paragraphs)
    assert text.startswith(f"Questions about this policy or a privacy request (including about an alumni entry): {privacy.PRIVACY_EMAIL}.")
    contact_links = [a["href"] for p in paragraphs for a in p.find_all("a", href=True)]
    assert contact_links == [f"mailto:{privacy.PRIVACY_EMAIL}"]


def test_the_policys_own_links_are_https_and_point_where_it_says():
    body = "\n".join(privacy.content(repo_url=render.REPO_URL,
                                      correction_url="https://github.com/x/y/issues/new?template=alumni-correction.yml"))
    hrefs = [h for h in re.findall(r'href="([^"]+)"', body) if not h.startswith("mailto:")]
    assert hrefs and all(h.startswith("https://") for h in hrefs)
    assert privacy.GITHUB_PRIVACY in hrefs and render.REPO_URL + "/security/advisories/new" in hrefs
