"""Monon Bell heist history: validation of the curated file, the ratings, the tally, the page order, the game lookups,
the links."""
import copy
import datetime as dt
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import facts  # noqa: E402
import heists  # noqa: E402
import render  # noqa: E402

NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
SRC = [{"label": "A source", "url": "https://example.com/story"}]


def incident(iid, year, outcome="stolen", **kw):
    base = {"id": iid, "year": year, "school": "not-recorded", "outcome": outcome, "rating": "reported",
            "title": f"Title {iid}", "summary": "A summary.", "counted_in_theft_total": outcome == "stolen",
            "sources": copy.deepcopy(SRC)}
    base.update(kw)
    return base


def small(incidents):
    return {"schema_version": 1, "verified_at": "2026-10-30", "intro": "Intro.", "method": ["Method."],
            "guarding": {"paragraphs": ["Guarded."], "sources": copy.deepcopy(SRC)}, "incidents": incidents}


SAMPLE = small([
    incident("late", 2012, "foiled", school="depauw"),
    incident("unknown", None, "foiled", school="depauw"),
    incident("early", 1941),
    incident("big", 1965, school="wabash", rating="documented", details=["Long story."], consequences="It went home."),
    incident("middle", 1959),
    incident("buried", 1966, "hidden", school="depauw", game_year=1967),
])


def game(date, result, depauw, wabash, **kw):
    return {"date": date, "result": result, "depauw": depauw, "wabash": wabash, "location": "home", **kw}


GAMES = [game("1965-11-13", "L", 6, 16), game("1967-11-11", "L", 0, 7), game("2012-11-10", "T", 7, 7)]


def real():
    return heists.load_json(ROOT / "data" / "bell-heists.json")


def test_committed_file_is_valid_and_every_story_has_a_source_and_unique_anchor():
    data = heists.validate(real())
    ids = [i["id"] for i in data["incidents"]]
    assert len(ids) == len(set(ids)) and all(i["sources"] for i in data["incidents"])


def test_missing_source_bad_outcome_and_duplicate_id_are_refused():
    broken = copy.deepcopy(SAMPLE)
    broken["incidents"][0]["sources"] = []
    with pytest.raises(heists.HeistError, match="sources"):
        heists.validate(broken)
    broken = copy.deepcopy(SAMPLE)
    broken["incidents"][0]["sources"] = [{"label": "x", "url": "not a url"}]
    with pytest.raises(heists.HeistError, match="URL"):
        heists.validate(broken)
    broken = copy.deepcopy(SAMPLE)
    broken["incidents"][0]["outcome"] = "borrowed"
    with pytest.raises(heists.HeistError, match="outcome"):
        heists.validate(broken)
    broken = copy.deepcopy(SAMPLE)
    broken["incidents"][1]["id"] = broken["incidents"][0]["id"]
    with pytest.raises(heists.HeistError, match="duplicate"):
        heists.validate(broken)


def test_years_summaries_and_the_theft_flag_are_checked():
    for field, value, match in (("year", 1700, "year"), ("year", True, "year"), ("summary", "  ", "summary"),
                                ("counted_in_theft_total", True, "theft total")):
        broken = copy.deepcopy(SAMPLE)
        broken["incidents"][0][field] = value        # incident 0 is a foiled attempt: it cannot count as a theft
        with pytest.raises(heists.HeistError, match=match):
            heists.validate(broken)
    assert heists.validate(copy.deepcopy(SAMPLE))


def test_every_story_needs_one_of_the_four_ratings():
    assert list(heists.RATINGS) == ["documented", "reported", "lore", "disputed"]
    for value in (None, "Documented", "rumor", ""):
        broken = copy.deepcopy(SAMPLE)
        if value is None:
            del broken["incidents"][0]["rating"]
        else:
            broken["incidents"][0]["rating"] = value
        with pytest.raises(heists.HeistError, match="rating"):
            heists.validate(broken)
    for value in heists.RATINGS:
        fine = copy.deepcopy(SAMPLE)
        fine["incidents"][0]["rating"] = value
        assert heists.validate(fine)
    assert all(i["rating"] in heists.RATINGS for i in real()["incidents"])


def test_the_game_year_to_show_must_be_another_real_year():
    for index, value in ((5, 1966), (5, "1967"), (5, True), (5, 1700), (1, 1993)):   # 5 is the burial, 1 has no year
        broken = copy.deepcopy(SAMPLE)
        broken["incidents"][index]["game_year"] = value
        with pytest.raises(heists.HeistError, match="game_year"):
            heists.validate(broken)


def test_the_tally_is_worked_out_from_the_incidents_and_says_at_least():
    counts = heists.tally(SAMPLE)
    assert counts == {"stolen": 3, "foiled": 2, "hidden": 1}
    assert heists.tally_text(counts) == "Stolen at least 3 times · 2 foiled attempts · 1 hidden by its own fans"
    sample = copy.deepcopy(SAMPLE)
    sample["incidents"].append(incident("another", 1988))
    assert heists.tally(sample)["stolen"] == 4          # adding a year changes the count, nothing else does
    assert heists.tally_text({"stolen": 1, "foiled": 0, "hidden": 0}) == "Stolen at least 1 time"


def test_the_real_tally_comes_from_the_file():
    data = real()
    assert heists.tally(data)["stolen"] == sum(1 for i in data["incidents"] if i["outcome"] == "stolen")


def test_incidents_run_oldest_to_newest_with_date_unknown_last():
    years = [i["year"] for i in heists.ordered(SAMPLE)]
    assert years == [1941, 1959, 1965, 1966, 2012, None]
    real_years = [i["year"] for i in heists.ordered(real())]
    dated = [y for y in real_years if y is not None]
    assert dated == sorted(dated) and real_years == dated + [None] * (len(real_years) - len(dated))
    same_year = small([incident("second", 1966, date="1966-11-19"), incident("first", 1966, date="1966-11-12"),
                       incident("undated-a", 1966), incident("undated-b", 1966)])
    assert [i["id"] for i in heists.ordered(same_year)] == ["undated-a", "undated-b", "first", "second"]


def test_the_game_result_comes_from_the_series_data_and_is_skipped_without_a_game():
    assert heists.game_line(heists.bell_game(GAMES, 1965)) == "That year’s Bell game: Wabash 16, DePauw 6"
    assert heists.game_line(heists.bell_game(GAMES, 2012)) == "That year’s Bell game: Tie 7–7"
    assert heists.bell_game(GAMES, 1941) is None                       # before the Bell game
    assert heists.bell_game(GAMES, 1999) is None
    assert heists.bell_game([game("1965-01-01", "L", 6, 16, date_known=False)], 1965) is None
    changed = [game("1965-11-13", "W", 21, 14)]
    assert heists.game_line(heists.bell_game(changed, 1965)) == "That year’s Bell game: DePauw 21, Wabash 14"


def test_a_story_can_show_another_years_game_and_the_line_names_that_year():
    assert heists.game_line(heists.bell_game(GAMES, 1967), 1967) == "The 1967 Bell game: Wabash 7, DePauw 0"
    html = page(GAMES)
    buried = html[html.index('id="heist-buried"'):html.index("</section>", html.index('id="heist-buried"'))]
    assert "The 1967 Bell game: Wabash 7, DePauw 0" in buried and "That year’s" not in buried
    no_game = page([game("1965-11-13", "L", 6, 16)])              # the named year has no game: no line at all
    assert "Bell game" not in no_game[no_game.index('id="heist-buried"'):no_game.index('id="heist-late"')]
    burial = next(i for i in real()["incidents"] if i["outcome"] == "hidden" and i["school"] == "depauw")
    assert burial["year"] + 1 == burial["game_year"]              # buried one November, dug up at the next game


def test_a_football_game_before_the_first_bell_game_is_never_a_cards_bell_game():
    """The schools played football for decades before the Bell (1932). A story from one of those years has a football
    game that year, and the card must not call it "that year's Bell game"."""
    first = facts.BELL_START
    before = [game(f"{first - 17}-11-20", "L", 0, 34), game(f"{first - 1}-11-21", "W", 13, 7)]
    since = [game(f"{first}-11-19", "T", 0, 0), game(f"{first + 1}-11-18", "W", 14, 0)]
    for played in before:
        assert heists.bell_game(before + since, int(played["date"][:4])) is None
    for played in since:
        assert heists.bell_game(before + since, int(played["date"][:4])) == played
    assert heists.bell_game(before, first - 1) is None and heists.bell_game(before + since, first + 2) is None
    stories = small([incident("prebell", first - 1), incident("firstbell", first),
                     incident("later", first + 1, game_year=first - 1)])            # a card that shows another year's game
    cards = BeautifulSoup(page(before + since, stories), "html.parser")
    assert "Bell game" not in cards.find(id="heist-prebell").get_text()
    assert "Bell game" not in cards.find(id="heist-later").get_text()
    assert "That year’s Bell game: Tie 0–0" in cards.find(id="heist-firstbell").get_text(" ", strip=True)



def page(games, data=SAMPLE):
    return "\n".join(heists.render_main(data, games, back_html="", game_href="../#football"))


def test_every_story_has_its_own_card_with_an_anchor_a_rating_and_sources():
    html = page(GAMES)
    for iid in ("heist-late", "heist-unknown", "heist-big", "heist-buried", "heist-early", "heist-middle"):
        assert f'id="{iid}"' in html and f'href="#{iid}"' in html
    assert "Other recorded thefts" not in html and "other-recorded-thefts" not in html   # no shared card of bare years
    assert html.count("Sources: ") == 6 + 1                              # six cards, plus the guarding section
    cards = html[html.index('id="timeline-h"'):]                         # the legend above shows each label once more
    assert cards.count('<span class="heist-rating heist-rating-reported">Reported</span>') == 5
    assert cards.count('<span class="heist-rating heist-rating-documented">Documented</span>') == 1
    big = html[html.index('id="heist-big"'):html.index("</section>", html.index('id="heist-big"'))]
    assert ('<span class="heist-tag heist-stolen">Stolen</span> '
            '<span class="heist-rating heist-rating-documented">Documented</span> ') in big   # beside the outcome tag
    assert html.count('<details class="heist-details">') == 1            # only the story with details
    assert "Aftermath" in html and "It went home." in html
    assert "That year’s Bell game: Wabash 16, DePauw 6" in html
    assert 'href="../#football"' in html


def test_no_tag_line_can_wrap_with_a_separator_at_the_start_of_a_line():
    assert heists.SEPARATOR == " · "                 # held to the item before it by a no-break space
    for data in (SAMPLE, real()):
        metas = re.findall(r'<p class="heist-meta">(.*?)</p>', page(GAMES, data))
        assert len(metas) == len(data["incidents"])
        for meta in metas:
            assert meta.count("·") >= 1 and meta.count(" ·") == meta.count("·"), meta
            assert re.search(' ·</span> <span class="heist-who">[^<]+</span>$', meta), meta   # who comes last
    noted = page(GAMES, small([incident("noted", 1998, date="1998-10-31", date_note="Halloween")]))
    assert '<span class="heist-when">Oct 31, 1998 · Halloween ·</span>' in noted


def test_page_order_is_tally_ratings_timeline_then_date_unknown_and_guarding():
    html = page(GAMES)
    order = [html.index(x) for x in ('id="tally"', 'id="ratings"', 'id="timeline-h"', 'id="heist-early"',
                                      'id="heist-middle"', 'id="heist-big"', 'id="heist-buried"', 'id="heist-late"',
                                      'id="date-unknown-h"', 'id="heist-unknown"', 'id="guarded-now"')]
    assert order == sorted(order)
    assert 'id="date-unknown-h"' not in page(GAMES, real())            # every story on the page has a year now


def test_the_legend_under_the_tally_is_a_definition_list_of_the_four_ratings_in_order():
    html = page(GAMES)
    soup = BeautifulSoup(html, "html.parser")
    (legend,) = soup.select(".heist-ratings")
    assert legend.name == "section" and legend["id"] == "ratings"
    label = soup.find(id=legend["aria-labelledby"])
    assert label.parent is legend and label.get_text() == heists.RATINGS_QUESTION == "How sure is each story?"
    (listing,) = legend.find_all("dl")
    assert [child.name for child in listing.find_all(recursive=False)] == ["dt", "dd"] * 4
    terms, meanings = listing.find_all("dt"), listing.find_all("dd")
    assert [term.get_text() for term in terms] == ["Documented", "Reported", "Lore", "Disputed"] == [
        label for label, _ in heists.RATINGS.values()]
    for term, key in zip(terms, heists.RATINGS):                         # each term is the tag the cards use
        (tag,) = term.find_all(recursive=False)
        assert tag.name == "span" and tag["class"] == ["heist-rating", f"heist-rating-{key}"]
        assert str(tag) == heists._rating(key)
    assert [meaning.get_text() for meaning in meanings] == [
        "A source from the time says so.",
        "A later, reputable source says so; no report from the time has been found.",
        "Repeated without a source.",
        "The sources disagree."]
    assert [meaning.get_text() for meaning in meanings] == [
        text[0].upper() + text[1:] + "." for _, text in heists.RATINGS.values()]
    assert html.index('id="tally"') < html.index('id="ratings"') < html.index('aria-label="About these stories"')


def test_the_legend_reuses_the_cards_rating_style_and_adds_no_color():
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert len(re.findall(r"^\.heist-rating \{", css, re.M)) == 1         # one rule draws the label on cards and legend
    rules = [line for line in css.splitlines() if line.startswith(".heist-ratings")]
    assert len(rules) == 3 and sum("color" in rule for rule in rules) == 1 and not any("#" in rule for rule in rules)
    assert "var(--color-text-muted)" in rules[0]                         # the muted text color the cards' tag line uses


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("heists") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "none.json")
    return out


def read(site, name):
    return (site / name).read_text(encoding="utf-8")


def test_every_anchor_starts_with_a_letter_so_the_html_is_valid(site):
    html = read(site, "heist-details/index.html")
    ids = re.findall(r'(?<![\w-])id="([^"]+)"', html)
    assert ids and all(i[0].isalpha() for i in ids) and len(ids) == len(set(ids))
    assert 'id="heist-1965-operation-frijoles"' in html


def test_the_page_is_built_with_the_strict_csp_athletics_nav_and_share_tags(site):
    html = read(site, "heist-details/index.html")
    assert "script-src" not in html and "<script" not in html
    assert '<a href="../" aria-current="page">Athletics</a>' in html
    assert "<title>The Bell’s heist history | DePauw vs. Wabash</title>" in html
    assert f'<link rel="canonical" href="{render.SITE_URL}heist-details/">' in html
    assert 'property="og:image"' in html
    assert "Stolen at least" in html and "Please let" not in html
    assert f"{render.SITE_URL}heist-details/" in read(site, "sitemap.xml")
    assert (site / "data" / "bell-heists.json").exists()


def test_the_football_card_and_the_joke_page_link_to_the_history(site):
    home = read(site, "index.html")
    assert '<a href="heist-details/">The Bell’s heist history</a>' in home
    assert home.count("The Bell’s heist history") == 1                       # one link, in the Fun facts list
    bell = home[home.index('id="monon-bell"'):home.index("</section>", home.index('id="monon-bell"'))]
    assert "heist-details" not in bell and "Read more on Wikipedia" in bell   # no longer in the Monon Bell block
    football = home[home.index('id="football"'):home.index("</article>", home.index('id="football"'))]
    start = football.index('<div class="fun-facts">')
    last_item = football[start:football.index("</ul>", start)].split("<li>")[-1]
    assert last_item.startswith('<span class="fun-label">Bell heists:</span> Students from both schools have tried to steal the Bell for decades.')
    assert '<a href="heist-details/">The Bell’s heist history</a>' in last_item
    assert football.index("Read more on Wikipedia") < football.index("heist-details/")   # the Bell block still comes first
    joke = read(site, "bell-heist/index.html")
    assert '<a href="../heist-details/">Read the real heist history</a>' in joke
    assert "Mission accomplished" in joke and "An entirely fictional mission." in joke


def test_the_correction_link_prefills_the_data_correction_form():
    url = heists.correction_url()
    assert "template=data-correction.yml" in url and "sport=Monon%20Bell%20heist%20history" in url
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "data-correction.yml").read_text(encoding="utf-8")
    assert heists.CORRECTION_SPORT in form


def test_no_internal_link_on_the_new_pages_points_to_a_missing_page(site):
    for name in ("heist-details/index.html", "bell-heist/index.html"):
        text = read(site, name)
        for href in re.findall(r'href="([^"#:]*)(?:#([^"]*))?"', text):
            path, fragment = href
            if path.startswith("http") or not path and not fragment:
                continue
            target = (site / name).parent / path if path else site / name
            target = target / "index.html" if target.is_dir() else target
            assert target.exists(), (name, path)
            if fragment:
                assert f'id="{fragment}"' in target.read_text(encoding="utf-8"), (name, path, fragment)


def test_the_athletics_how_it_works_page_mentions_the_curated_heist_history(site):
    html = read(site, "how-it-works/athletics/index.html")
    assert 'href="../../heist-details/"' in html and "not updated automatically" in html


def test_the_page_says_the_record_is_incomplete_once_and_the_intro_note_does_not_repeat_it():
    real_data = real()
    html = page(GAMES, real_data)
    assert html.lower().count("record is incomplete") == 1             # the method paragraph, and nowhere else
    assert "heist-note" not in html and "Who took the Bell is unknown" not in html
    assert "Associated game" not in html


def test_no_story_rests_on_wikipedia_and_archived_copies_say_so():
    data = real()
    sources = list(data["guarding"]["sources"]) + [s for i in data["incidents"] for s in i["sources"]]
    text = " ".join(data["method"] + [data["intro"]])
    assert "Wikipedia" not in text and not any("wikipedia" in (s["url"] + s["label"]).lower() for s in sources)
    archived = [s for s in sources if s["url"].startswith("https://web.archive.org/web/")]
    assert archived
    for source in sources:                         # the label says "(archived copy)" exactly when the link is one
        assert source["label"].endswith("(archived copy)") == (source in archived), source["label"]
    assert not any("archive.org/web" in s["url"] for s in sources if s not in archived)


def test_no_aftermath_line_only_repeats_that_years_game_score():
    for incident in real()["incidents"]:
        text = incident.get("consequences", "")
        assert not re.search(r"\b(won|lost|beat|tied)\b[^.]*\d+\s*[–-]\s*\d+", text), incident["id"]
