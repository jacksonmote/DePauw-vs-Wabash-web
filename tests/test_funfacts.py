"""The tiger's fun-fact data: validation, balance and the date-based fallback pick. No clock, no network."""
import copy
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import funfacts  # noqa: E402


def fact(fid, school="depauw", text="A short fact.", url="https://example.com/a"):
    return {"id": fid, "school": school, "text": text, "sources": [{"label": "Source", "url": url}]}


def data(*facts):
    return {"schema_version": 1, "verified_at": "2026-10-31", "facts": list(facts)}


GOOD = data(fact("a"), fact("b", "wabash"), fact("c", "both"))


def real():
    return funfacts.load_json(ROOT / "data" / "fun-facts.json")


def test_a_complete_balanced_file_validates():
    assert funfacts.validate(copy.deepcopy(GOOD)) is not None
    assert funfacts.school_counts(GOOD) == {"depauw": 1, "wabash": 1, "both": 1}


def test_the_committed_file_is_valid_and_balanced_with_a_source_on_every_fact():
    value = funfacts.validate(real())
    counts = funfacts.school_counts(value)
    assert abs(counts["depauw"] - counts["wabash"]) <= 1 and counts["both"] >= 3
    assert len(value["facts"]) >= 20
    for f in value["facts"]:
        assert f["sources"] and all(s["url"].startswith("https://") for s in f["sources"])
        assert len(f["text"]) <= funfacts.MAX_TEXT and f["text"].strip()


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["facts"][0].pop("sources"), "sources"),
    (lambda d: d["facts"][0].update(sources=[]), "sources"),
    (lambda d: d["facts"][0]["sources"][0].update(url="http://example.com/a"), "https"),
    (lambda d: d["facts"][0]["sources"][0].update(url="https://example.com/a b"), "https"),
    (lambda d: d["facts"][0]["sources"][0].update(label=" "), "label"),
    (lambda d: d["facts"][1].update(id="a"), "duplicate"),
    (lambda d: d["facts"][0].update(id="Has Space"), "lowercase"),
    (lambda d: d["facts"][0].update(text="  "), "text"),
    (lambda d: d["facts"][0].update(text="x" * (funfacts.MAX_TEXT + 1)), "limit"),
    (lambda d: d["facts"][0].update(school="alumni"), "school"),
    (lambda d: d.update(schema_version=2), "schema_version"),
    (lambda d: d.update(verified_at="Oct 31"), "verified_at"),
    (lambda d: d.update(facts=[]), "facts"),
])
def test_validation_refuses_each_kind_of_bad_data(mutate, message):
    bad = copy.deepcopy(GOOD)
    mutate(bad)
    with pytest.raises(funfacts.FunFactsError, match=message):
        funfacts.validate(bad)


def test_the_schools_may_differ_by_one_but_not_two_and_both_facts_do_not_count():
    funfacts.validate(data(fact("a"), fact("b"), fact("c", "wabash")))
    with pytest.raises(funfacts.FunFactsError, match="unbalanced"):
        funfacts.validate(data(fact("a"), fact("b"), fact("c"), fact("d", "both"), fact("e", "wabash")))
    funfacts.validate(data(fact("a"), fact("b", "wabash"), *[fact(f"both-{i}", "both") for i in range(5)]))


def test_the_fallback_pick_depends_only_on_the_date_and_changes_day_to_day():
    day = dt.date(2026, 11, 14)
    assert funfacts.fallback_index(23, day) == funfacts.fallback_index(23, day)
    assert funfacts.fallback_index(23, day) == day.toordinal() % 23
    picks = [funfacts.fallback_index(23, day + dt.timedelta(days=n)) for n in range(23)]
    assert sorted(picks) == list(range(23))                       # every fact comes up once a cycle
    assert funfacts.fallback_index(1, day) == 0
    with pytest.raises(funfacts.FunFactsError):
        funfacts.fallback_index(0, day)
