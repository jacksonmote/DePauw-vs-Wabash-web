"""Tests for the direct links to each card and section."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
from test_meetings import entry, page, sched  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def full_page(tmp_path):
    return page(tmp_path, {"football": entry(sched("2026-11-14"))})


def test_every_card_and_section_has_an_id_from_its_key(tmp_path):
    html = full_page(tmp_path)
    for anchor in ("football", "mens-basketball", "mens-lacrosse", "monon-bell", "next-meeting"):
        assert f'id="{anchor}"' in html, anchor


def test_each_heading_text_is_the_link_to_its_own_section(tmp_path):
    html = full_page(tmp_path)
    assert '<h2><a class="permalink" id="football-h" href="#football">Football</a></h2>' in html
    assert '<a class="permalink" id="mens-basketball-h" href="#mens-basketball">Men’s Basketball</a>' in html
    assert '<h3><a class="permalink" id="monon-bell-h" href="#monon-bell">The Monon Bell</a></h3>' in html
    assert '<a class="permalink" id="next-meeting-h" href="#next-meeting">Next meeting</a>' in html
    assert "aria-label=\"Link to" not in html and ">#</a>" not in html   # no separate '#' marker


def test_every_permalink_points_at_an_element_that_exists(tmp_path):
    html = full_page(tmp_path)
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    targets = re.findall(r'class="permalink" id="[^"]+" href="#([^"]+)"', html)
    assert len(targets) >= 5 and all(t in ids for t in targets)
    assert len(re.findall(r'\bid="([^"]+)"', html)) == len(ids)   # no duplicate ids


def test_a_card_is_named_by_its_heading_link_text(tmp_path):
    html = full_page(tmp_path)
    assert 'aria-labelledby="football-h"' in html and 'id="football-h" href="#football">Football</a>' in html


def test_canonical_and_og_url_stay_on_the_home_page(tmp_path):
    html = full_page(tmp_path)
    assert f'<link rel="canonical" href="{render.SITE_URL}">' in html
    assert f'<meta property="og:url" content="{render.SITE_URL}">' in html
    assert "#" not in render.SITE_URL


def test_linked_sections_leave_room_at_the_top_of_the_screen():
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    rule = re.search(r"([^{}]*)\{[^}]*scroll-margin-top[^}]*\}", css).group(1)
    for selector in (".sport", ".next-meeting", ".bell"):
        assert selector in rule


def test_home_page_loads_only_the_deferred_same_origin_script(tmp_path):
    assert full_page(tmp_path).count('<script src="easter-eggs.js" defer></script>') == 1
