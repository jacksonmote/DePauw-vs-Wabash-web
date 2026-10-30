"""Tests for the link-preview card, the icons, the manifest and the tags that point at them."""
import hashlib
import html
import io
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import images  # noqa: E402
import render  # noqa: E402
from test_render import build, football, lacrosse, series, write  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOTALS = {"depauw_wins": 359, "wabash_wins": 238, "ties": 16, "games_played": 613}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.metas = [], []

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            self.links.append(dict(attrs))
        elif tag == "meta":
            self.metas.append(dict(attrs))


def tags(html):
    p = Tags()
    p.feed(html)
    return p


def meta(p, **match):
    return [m for m in p.metas if all(m.get(k) == v for k, v in match.items())]


def site_path(out, ref):
    """Map a link in the page or manifest to the file it must be in site/ (absolute site URLs included)."""
    ref = ref.replace(render.SITE_URL, "")
    return out / ref.split("?")[0].lstrip("/")


# ---- the files -------------------------------------------------------------- #

def test_every_image_and_icon_the_pages_and_manifest_name_exists_at_its_stated_size(tmp_path):
    out, index = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse()})
    checked = []
    for html in (index, (out / "404.html").read_text(encoding="utf-8")):
        p = tags(html)
        for link in p.links:
            if link.get("rel") in ("icon", "apple-touch-icon"):
                f = site_path(out, link["href"])
                assert f.exists(), link
                if f.suffix == ".png":
                    w, h = (int(n) for n in link["sizes"].split("x"))
                    assert Image.open(f).size == (w, h), link
                    checked.append(f.name)
            if link.get("rel") == "manifest":
                assert site_path(out, link["href"]).exists()
    p = tags(index)
    og = meta(p, property="og:image")[0]["content"]
    img = Image.open(site_path(out, og))
    assert img.size == (int(meta(p, property="og:image:width")[0]["content"]),
                        int(meta(p, property="og:image:height")[0]["content"])) == (1200, 630)
    assert meta(p, name="twitter:image")[0]["content"] == og
    manifest = json.loads((out / "site.webmanifest").read_text(encoding="utf-8"))
    for icon in manifest["icons"]:
        w, h = (int(n) for n in icon["sizes"].split("x"))
        f = out / icon["src"]
        assert f.exists() and Image.open(f).size == (w, h), icon
        checked.append(f.name)
    assert {"apple-touch-icon.png", "favicon-32.png", "icon-192.png", "icon-512.png",
            "icon-maskable-512.png"} <= set(checked)


def test_the_og_image_url_is_absolute_and_versioned_by_the_totals(tmp_path):
    _, index = build(tmp_path, {"football": football()})
    url = meta(tags(index), property="og:image")[0]["content"]
    assert re.fullmatch(r"https://depauwvswabash\.com/og-image\.png\?v=[0-9a-f]{8}", url)
    other_dir = tmp_path / "b"
    other_dir.mkdir()
    render.build(write(other_dir, series({"football": football(), "mens-lacrosse": lacrosse()})), other_dir / "site")
    other = meta(tags((other_dir / "site" / "index.html").read_text(encoding="utf-8")), property="og:image")
    assert url != other[0]["content"]               # the totals changed, so the version did
    assert images.og_version(TOTALS, "DePauw") != images.og_version(TOTALS, "Wabash")
    assert images.og_version(TOTALS, "DePauw") == images.og_version(dict(TOTALS), "DePauw")


def test_the_card_is_identical_every_time_and_small():
    a, b = images.og_image(TOTALS, "DePauw"), images.og_image(TOTALS, "DePauw")
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()
    assert len(a) < 300_000
    assert images.og_image(TOTALS, None) != a and images.og_image(dict(TOTALS, ties=0), "DePauw") != a
    assert images.icon_png(180) == images.icon_png(180)


def test_png_files_carry_no_metadata():
    for data in (images.og_image(TOTALS, "DePauw"), images.icon_png(192)):
        img = Image.open(io.BytesIO(data))
        assert img.format == "PNG" and set(img.info) <= {"transparency"}
        assert b"tEXt" not in data and b"iTXt" not in data and b"tIME" not in data


def test_the_font_and_its_license_ship_with_the_repo_and_pillow_is_pinned():
    assert images.BOLD.exists() and images.MEDIUM.exists()
    assert "SIL Open Font License" in (ROOT / "static" / "fonts" / "OFL.txt").read_text(encoding="utf-8")
    assert re.search(r"(?m)^Pillow==\d+\.\d+\.\d+$", (ROOT / "requirements.txt").read_text())


def test_text_is_laid_out_with_the_basic_engine_so_every_platform_agrees():
    assert images.font(images.BOLD, 40).layout_engine == ImageFont.Layout.BASIC


# ---- icons ------------------------------------------------------------------ #

def test_apple_touch_icon_is_opaque_and_full_bleed():
    img = Image.open(io.BytesIO(images.icon_png(180)))
    assert img.size == (180, 180) and img.mode == "RGB"             # no alpha channel for iOS to mishandle
    assert img.getpixel((0, 0)) == img.getpixel((179, 179)) == images.BG


def test_the_maskable_icon_keeps_its_artwork_inside_the_safe_circle():
    img = Image.open(io.BytesIO(images.icon_png(512, maskable=True))).convert("RGB")
    for x in range(0, 512, 4):
        for y in range(0, 512, 4):
            if img.getpixel((x, y)) != images.BG:
                assert (x - 256) ** 2 + (y - 256) ** 2 <= (0.4 * 512) ** 2, (x, y)


def test_the_card_has_no_transparency():
    img = Image.open(io.BytesIO(images.og_image(TOTALS, "DePauw"))).convert("RGBA")
    assert img.size == (1200, 630) and img.getextrema()[3] == (255, 255)


# ---- tags and manifest ------------------------------------------------------ #

def test_share_tags_are_complete_and_built_from_the_live_totals(tmp_path):
    _, index = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse()})
    p = tags(index)

    def get(**k):
        return meta(p, **k)[0]["content"]

    assert get(property="og:type") == "website" and get(property="og:site_name") == "DePauw vs. Wabash"
    assert get(property="og:url") == render.SITE_URL
    assert get(property="og:image:width") == "1200" and get(property="og:image:height") == "630"
    assert "holds the Monon Bell" in get(property="og:image:alt")
    assert get(name="twitter:card") == "summary_large_image"
    assert get(property="og:description") == get(name="twitter:description") == get(name="description")
    assert get(property="og:title") == get(name="twitter:title") == render.TITLE
    assert "DePauw 5 wins" in get(property="og:description")
    assert "DePauw 5 wins" in get(property="og:image:alt")


def test_theme_colors_follow_light_and_dark_mode(tmp_path):
    out, index = build(tmp_path, {"football": football()})
    for html in (index, (out / "404.html").read_text(encoding="utf-8")):
        p = tags(html)
        assert meta(p, name="theme-color", media="(prefers-color-scheme: light)")[0]["content"] == "#111111"
        assert meta(p, name="theme-color", media="(prefers-color-scheme: dark)")[0]["content"] == "#000000"


def test_every_page_links_every_icon_including_the_404_with_root_relative_paths(tmp_path):
    out, index = build(tmp_path, {"football": football()})
    p404 = tags((out / "404.html").read_text(encoding="utf-8"))
    assert {"/favicon.svg", "/favicon-32.png", "/apple-touch-icon.png", "/site.webmanifest"} <= {
        link["href"] for link in p404.links}
    assert {"favicon.svg", "favicon-32.png", "apple-touch-icon.png", "site.webmanifest"} <= {
        link["href"] for link in tags(index).links}
    assert not meta(p404, property="og:image")        # the 404 is noindex and has no card


def test_manifest_fields(tmp_path):
    out, _ = build(tmp_path, {"football": football()})
    m = json.loads((out / "site.webmanifest").read_text(encoding="utf-8"))
    assert m["name"] == "DePauw vs. Wabash" and m["short_name"] and m["start_url"] == "/"
    assert m["display"] == "browser" and m["theme_color"] == "#111111" and m["background_color"] == "#f6f4ee"
    assert [(i["sizes"], i.get("purpose")) for i in m["icons"]] == [
        ("192x192", None), ("512x512", None), ("512x512", "maskable")]


def test_the_csp_allows_the_manifest_and_nothing_extra(tmp_path):
    _, index = build(tmp_path, {"football": football()})
    csp = html.unescape(re.search(r"Content-Security-Policy\" content=\"([^\"]+)\"", index).group(1))
    assert csp == render.CSP + "; script-src 'self'"
    assert "style=" not in index                      # style-src 'self' would block inline styles


ALUMNI_VALUES = [("DePauw", "$516,000"), ("Wabash", "$511,000")]


def test_the_alumni_card_is_1200_by_630_deterministic_small_and_without_metadata():
    a, b = images.alumni_og_image(ALUMNI_VALUES), images.alumni_og_image(ALUMNI_VALUES)
    assert a == b and len(a) < 300_000
    img = Image.open(io.BytesIO(a))
    assert img.size == (1200, 630) and img.format == "PNG" and set(img.info) <= {"transparency"}
    assert a != images.og_image(TOTALS, "DePauw")
    changed = [("DePauw", "$544,000"), ("Wabash", "$511,000")]
    assert images.alumni_og_image(changed) != a
    assert images.alumni_og_image(ALUMNI_VALUES, even=True) != a             # the caption says "about even"
    assert images.alumni_og_version(ALUMNI_VALUES, True) != images.alumni_og_version(ALUMNI_VALUES)
    assert images.alumni_og_version(ALUMNI_VALUES) == images.alumni_og_version(list(ALUMNI_VALUES))
    assert images.alumni_og_version(ALUMNI_VALUES) != images.alumni_og_version(changed)


def test_the_alumni_page_has_a_complete_large_share_card_that_exists_at_its_stated_size(tmp_path):
    out, _ = build(tmp_path, {"football": football()})
    page = (out / "alumni" / "index.html").read_text(encoding="utf-8")
    p = tags(page)
    og = meta(p, property="og:image")[0]["content"]
    assert re.fullmatch(r"https://depauwvswabash\.com/alumni-og-image\.png\?v=[0-9a-f]{8}", og)
    assert meta(p, name="twitter:card")[0]["content"] == "summary_large_image"
    assert meta(p, name="twitter:image")[0]["content"] == og
    assert meta(p, property="og:image:type")[0]["content"] == "image/png"
    alt = meta(p, property="og:image:alt")[0]["content"]
    assert "$516,000" in alt and "$511,000" in alt and "about even" in alt and "leads" not in alt.lower()
    img = Image.open(site_path(out, og))
    assert img.size == (int(meta(p, property="og:image:width")[0]["content"]),
                        int(meta(p, property="og:image:height")[0]["content"])) == (1200, 630)
    home = meta(tags((out / "index.html").read_text(encoding="utf-8")), property="og:image")[0]["content"]
    assert home != og and "og-image.png" in home                # the athletics card is untouched
