"""
The "How the numbers work" pages, written for fans. Three pages share render.py's how_page() shell:
  /how-it-works/            landing_content():       one line and a link for each of the two pages below
  /how-it-works/athletics/  content():               the same facts as the README's merge section
  /how-it-works/alumni/     alumni_content():        who counts, sourcing, the endowment basis, enrollment,
                                                     rounding, the "about even" rule, the size and history note
Every number comes from the data (alumni.json and endowment.json through the alumni.py functions, series.json,
overrides.json, or the constants in merge.py and gameday.py that the code actually uses), never from this file,
so a page cannot go out of date when the data changes.

render.py supplies the page shell and footer.
"""

from __future__ import annotations

import html
from fractions import Fraction

import alumni as alumni_page
import gameday
import merge

TITLE = "How the athletics numbers work | DePauw vs. Wabash"
H1 = "How the numbers work"                         # the landing page
H1_ATHLETICS = "How the athletics numbers work"     # the same words as TITLE and the landing-page link

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def days(n: int) -> str:
    return f"{n} day" + ("" if n == 1 else "s")


def placeholder_range() -> str:
    """'Jan 1 to Jan 3', from the placeholder days the merge treats as 'no real date'."""
    ds = sorted(merge.PLACEHOLDER_DAYS)
    name = lambda d: f"{MONTH_ABBR[d[0] - 1]} {d[1]}"
    return name(ds[0]) if len(ds) == 1 else f"{name(ds[0])} to {name(ds[-1])}"


def ruling_counts(rules: dict) -> tuple:
    """(same-game, different-games, several-days-of-one-meet rulings) across every sport, from the overrides."""
    same = sum(len(r.get("same_game", [])) for r in rules.values())
    diff = sum(len(r.get("different_games", [])) for r in rules.values())
    days_of_one = sum(len(r.get("session_of", [])) for r in rules.values())
    return same, diff, days_of_one


SWIMMING = "mens-swimming-and-diving"


def unit_note(sports: dict) -> str:
    """'tennis counts matches and swimming and diving counts meets': the sports that call a game something else."""
    notes = [f"{words(s['name'])} counts {s['unit'][1]}" for s in sports.values() if s.get("unit")]
    return " and ".join(notes)


def words(name: str) -> str:
    """"Men's Swimming and Diving" -> "swimming and diving"."""
    return name.replace("Men’s ", "").replace("Men's ", "").lower()


def swimming_lines(sports: dict) -> list:
    """The swimming and diving section, from the data. Empty when the sport is not on the site."""
    s = sports.get(SWIMMING)
    if not s:
        return []
    games = s["games"]
    half = sum(1 for g in games if any(isinstance(g.get(f), float) for f in ("depauw", "wabash")))
    unscored = sum(1 for g in games if g.get("score_known") is False)
    sessions = (s.get("sources") or {}).get("session_rows", 0)
    return [
        '<h3>Swimming and diving</h3>',
        '<p>Only dual meets, where DePauw’s team swims Wabash’s team head to head, are counted. Invitationals, '
        'relays, and conference championships have many teams in the pool at once, so there is no head-to-head '
        'result to add up, and they are left out of the records and out of “Next meeting”. '
        'The site tells a dual meet from a multi-team event by the name the school gives it and by how many '
        'opponents are listed.</p>',
        '<ul>',
        f'<li><strong>Half points:</strong> a meet can end with half a point on the board. Scores like 172.5 are '
        f'shown just as the schools list them (meets with a half point right now: {half:,}), and the point '
        'totals add them up exactly.</li>',
        f'<li><strong>Meets with no score:</strong> a school may list who won a meet without listing the score. '
        f'The meet still counts as a win or a loss, but it has no points to add to the totals, the biggest-win '
        f'list or the close-meet count, and the page says “score not recorded” (right now: {unscored:,}).</li>',
        '<li><strong>A score written the wrong way around:</strong> when a row says one school won but lists '
        'a bigger score for the other, the site trusts the win or loss and swaps the two scores, then says so '
        'beside the game. This is checked on every sport, not only swimming.</li>',
        f'<li><strong>A meet spread over several days:</strong> in March 2021 the two schools swam one '
        f'three-day meet. DePauw’s record has one meet; Wabash’s lists the running scores after each day as if '
        f'they were separate meets (rows set aside right now: {sessions:,}). A person read Wabash’s own write-ups '
        'and ruled that they are days of one meet, so it counts once, and the page shows the running scores '
        'beside it.</li>',
        '</ul>',
    ]


def totals(sports: dict) -> dict:
    keys = ("both", "depauw_only", "wabash_only", "disputed", "date_differs", "dates_not_recorded")
    return {k: sum(s.get("sources", {}).get(k, 0) for s in sports.values()) for k in keys}


def frequent_minutes() -> int:
    """Minutes between game-day runs, from the workflow's frequent schedule (e.g. '7,37 * * * *' is 30)."""
    return 60 // len(gameday.FREQUENT_CRON.split()[0].split(","))


def content(data: dict, rules: dict, kept: int, *, sources_html: str, not_included: str, repo_url: str) -> list:
    """The page's <main> section as lines of HTML. `rules` is merge.load_overrides, `kept` the number of
    keep_previous rulings, `sources_html` the same per-sport source links the footer shows."""
    sports = data["sports"]
    t = data["totals"]
    n = totals(sports)
    same, diff, days_of_one = ruling_counts(rules)
    names = [s["name"] for s in sports.values()]
    names_text = (" and ".join(names) if len(names) <= 2        # a serial comma from three items on
                  else ", ".join(names[:-1]) + f", and {names[-1]}")
    both_listed = n["both"]
    one_school = n["depauw_only"] + n["wabash_only"]
    overrides_url = f"{repo_url}/blob/main/data/overrides.json"
    out = [
        '<section class="prose" aria-labelledby="how-h">',
        f'<h2 id="how-h">{esc(H1_ATHLETICS)}</h2>',
        '<p>This site adds up every game DePauw and Wabash have played against each other. Here is where '
        'the numbers come from and what the site does when the two schools’ records do not line up.</p>',

        '<h3>Where the data comes from</h3>',
        '<p>Nothing is typed in by hand. The site reads each school’s own athletics website: the '
        '“opponent history” page for each sport (every game ever recorded against the other school) and '
        'the schedule page (upcoming games). Those pages are checked automatically, and you can open any '
        'of them yourself to compare:</p>',
        f'<ul class="sources">\n{sources_html}\n</ul>',

        '<h3>What is counted</h3>',
        f'<p>Only men’s sports that DePauw and Wabash play head to head: {esc(names_text)}. Wabash is an '
        f'all-male college, so women’s sports have no Wabash side to compare with. Left out: {esc(not_included)} '
        f'Together the sports above add up to {t["games_played"]:,} games so far'
        + (f' ({esc(unit_note(sports))}).</p>' if unit_note(sports) else '.</p>'),

        '<h3>How the two schools’ records are combined</h3>',
        '<p>Each school keeps its own list of these games, and the two lists are not always identical, so the '
        'site compares them game by game instead of trusting either one alone.</p>',
        '<ul>',
        '<li><strong>Same date and same score:</strong> the same game. Most games match this way.</li>',
        '<li><strong>Doubleheaders:</strong> two games on one date are paired up by score first, then in '
        'the order each school lists them.</li>',
        f'<li><strong>Small date differences and typos:</strong> a game with the same score listed up to '
        f'{esc(days(merge.MAX_DATE_GAP_DAYS))} apart is treated as one game, and so is one whose date has a single '
        'obvious slip (a digit wrong in the day, the month off by one or the year off by one).</li>',
        f'<li><strong>Only the year is known:</strong> some old games have no real date: a school lists them '
        f'on a placeholder such as {esc(placeholder_range())}, or on a date in a month the sport is not played. '
        f'Games with no recorded date right now: {n["dates_not_recorded"]:,}. They are shown with their '
        'season and “date not recorded”, matched by season and score, and never counted as the most '
        'recent game.</li>',
        f'<li><strong>Listed by one school only:</strong> a game that only one school lists still counts, and '
        f'is marked “Wabash record only” or “DePauw record only”. Right now both schools list {both_listed:,} '
        f'games and only one school lists {one_school:,} ({n["wabash_only"]:,} Wabash only, '
        f'{n["depauw_only"]:,} DePauw only).</li>',
        '</ul>',
        '<p>The site would rather leave a doubtful pair as two separate one-school games, which is visible '
        'and can be checked, than quietly merge two real games into one.</p>',

        *swimming_lines(sports),

        '<h3>Disputed games</h3>',
        f'<p>Sometimes the schools disagree on a score or even on who won. Disputed games right now: '
        f'{n["disputed"]:,}. In those cases DePauw’s version counts in the totals, so the totals come '
        'from one version and are repeatable. That is a convention, not a judgement that DePauw is right: '
        'every disputed game shows both versions, each labelled with whose record it is. '
        f'Games the two schools list on different dates are flagged as well ({n["date_differs"]:,} right '
        'now).</p>',

        '<h3>Rulings</h3>',
        f'<p>When the automatic rules cannot decide, a person checks the box scores and records a ruling, '
        f'with a reason. Rulings made so far: {same + diff + days_of_one:,} ({same:,} saying two entries '
        f'are the same game, {diff:,} saying they are different games'
        + (f', {days_of_one:,} saying several entries are days of one meet' if days_of_one else '')
        + f'). They are public: '
        f'<a href="{esc(overrides_url)}">data/overrides.json</a> on GitHub.</p>',

        '<h3>How often it updates</h3>',
        f'<p>The site checks both schools’ pages twice a day, and every {frequent_minutes()} minutes while a '
        'DePauw vs. Wabash game is being played. “Last checked” at the bottom of the home page is the last time '
        'the site reached the schools’ pages. “Data last changed” is the last time a result actually changed, '
        'which can be much earlier, because nothing changes between games. Checks run on GitHub’s servers '
        'and GitHub can start a scheduled run late, so a new result may take a while to appear.</p>',

        '<h3>Corrections</h3>',
        f'<p>If something looks wrong, use the “Report a data error” link on any sport card or in the footer. '
        'Changes to old games are held for review before they appear: if a school edits a result from a past season, '
        'the site keeps showing the old version until a person reviews the change'
        + (f' ({kept:,} rejected so far)' if kept else '')
        + '. Results from the current season update as soon as the school’s page does.</p>',

        '<h3>The Monon Bell heist history</h3>',
        '<p>The <a href="../../heist-details/">heist history page</a> is not part of the scraped records. Its '
        'stories are curated by hand from news reports, school publications and Wikipedia, each with its sources '
        'linked, and they are not updated automatically: they change only when someone finds a source and the '
        'change is reviewed.</p>',

        '<h3>Full technical details</h3>',
        f'<p>The exact matching rules, the numbers behind them and how to change them are written up in the '
        f'<a href="{esc(repo_url)}#readme">README on GitHub</a>.</p>',
        '</section>',
    ]
    return out


LANDING_TITLE = "How the numbers work | DePauw vs. Wabash"
LANDING_DESCRIPTION = ("How the numbers work on DePauw vs. Wabash: the head-to-head athletics records, and the "
                       "alumni and endowment comparison.")
ALUMNI_TITLE = "How the alumni numbers work | DePauw vs. Wabash"


def landing_content() -> list:
    """/how-it-works/: one line for each of the two pages, so the address that was already shared still works."""
    return [
        '<section class="prose" aria-labelledby="how-h">',
        f'<h2 id="how-h">{esc(H1)}</h2>',
        '<p>The site has two halves, and each has its own plain-language explanation.</p>',
        '<h3><a href="athletics/">How the athletics numbers work</a></h3>',
        '<p>Where the head-to-head records come from, how the two schools’ records are combined, and what '
        'happens when they disagree.</p>',
        '<h3><a href="alumni/">How the alumni numbers work</a></h3>',
        '<p>Who counts as a notable graduate, how names were sourced and checked, and how endowment per '
        'student is worked out.</p>',
        '</section>',
    ]


def _dollars(n: int) -> str:
    return f"${n:,}"


def _list_and(items: list) -> str:
    """'a', 'a and b', 'a, b, and c' (the site's serial comma)."""
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def alumni_content(data: dict, endowment: dict, *, repo_url: str) -> list:
    """/how-it-works/alumni/'s <main> section. Every number is read from alumni.json and endowment.json (or
    worked out from them with the same functions the alumni page uses), never typed here."""
    schools = alumni_page.SCHOOLS
    categories = alumni_page.visible_categories(data)
    by_cat = alumni_page.counts(data)
    n_people = {k: len({p["id"] for p in data["people"] if p["school"] == k}) for k in schools}
    n_multi = {k: sum(1 for p in data["people"] if p["school"] == k and len(p["categories"]) > 1) for k in schools}
    fy, as_of = endowment["fiscal_year"], alumni_page.fmt_date(endowment["as_of"])
    verified = alumni_page.fmt_date(max(data["verified_at"], endowment["verified_at"]))
    per = alumni_page.per_student(endowment)
    even = alumni_page.about_even(endowment)
    exact = {k: Fraction(alumni_page.comparable_amount(i), i["enrollment"]["count"])
             for k, i in endowment["schools"].items()}
    gap = (max(exact.values()) / min(exact.values()) - 1) * 100
    term = next(iter(endowment["schools"].values()))["enrollment"]["term"]
    basis = endowment["comparable_basis"]

    cat_items = [
        f'<li><strong>{esc(c["name"])}.</strong> {esc(c["definition"])} '
        f'DePauw {by_cat[c["id"]]["depauw"]}, Wabash {by_cat[c["id"]]["wabash"]}.</li>' for c in categories]

    adjusted, plain = [], []
    for key, name in schools.items():
        item = endowment["schools"][key]
        if item["comparable"]["excluded"]:
            parts = [f'{_dollars(p["amount_usd"])} of {esc(p["label"])}' for p in item["comparable"]["excluded"]]
            adjusted.append(
                f'{esc(name)} reports total endowment net assets of {_dollars(item["amount_usd"])}, which '
                f'includes {_list_and(parts)}. Taking those out leaves '
                f'{_dollars(alumni_page.comparable_amount(item))}.')
        else:
            plain.append(f'{esc(name)} reports {esc(basis)} of {_dollars(item["amount_usd"])}')
    basis_html = (' '.join(adjusted) + (' ' if adjusted and plain else '') +
                  (_list_and(plain) + ', so nothing is taken out.' if plain else ''))

    enrolled = ", ".join(f'{esc(name)} {endowment["schools"][key]["enrollment"]["count"]:,}'
                         for key, name in schools.items())
    enroll_links = " and ".join(
        f'<a href="{esc(endowment["schools"][key]["enrollment"]["source_url"])}">{esc(name)}’s College Navigator page</a>'
        for key, name in schools.items())
    figures = " and ".join(f'{esc(name)} {_dollars(per[key])}' for key, name in schools.items())
    even_html = (f'Right now the two figures are about {gap:.1f}% apart, so the page says “about even” and does '
                 f'not mark either school as higher.' if even else
                 f'Right now the two figures are about {gap:.1f}% apart, which is more than '
                 f'{alumni_page.EVEN_PERCENT}%, so the page marks the higher one.')
    return [
        '<section class="prose" aria-labelledby="how-h">',
        '<h2 id="how-h">How the alumni numbers work</h2>',
        f'<p>The alumni page lists named graduates of DePauw and Wabash in {len(categories)} award and '
        f'public-service categories, and compares the two schools’ endowments per student. Unlike the '
        f'athletics records, none of it updates by itself.</p>',

        '<h3>Who counts</h3>',
        '<p>Only people who <strong>earned a degree</strong> from the school count. Honorary degrees, attending '
        'without graduating, nominees, finalists, and people who were part of a winning team or film without an '
        'individual prize or office do not. Graduates of any gender count, under the school’s historical names too.</p>',
        f'<p>The two schools are not the same size or makeup. {esc(alumni_page.context_note(endowment))}</p>',
        '<p>A person counts <strong>once per school and category</strong>. Someone who was both a senator and a '
        'governor appears in both categories, once in each. There is <strong>no overall alumni score</strong>: '
        'the page never adds the categories together or says which school “wins”, because a bigger count in one '
        'category says nothing about another.</p>',
        f'<p>Right now the page names {n_people["depauw"]} DePauw graduates and {n_people["wabash"]} Wabash '
        f'graduates; {n_multi["depauw"]} and {n_multi["wabash"]} of them appear in more than one category. A '
        f'category with no verified graduate at either school is left off the page.</p>',

        '<h3>The categories</h3>',
        f'<ul>{"".join(cat_items)}</ul>',

        '<h3>How names were found and checked</h3>',
        f'<p>{esc(data["methodology"])}</p>',
        '<p>Every name carries a source for the degree and a source for the office or award, shown beside it on '
        'the alumni page. For Fortune 500 CEOs the page also shows the company, the ranking year and the rank, '
        'with a source for the ranking; the year shown is the ranking year, not the length of the term. Open '
        'terms read “present”.</p>'
        f'<p>These are verified named graduates in the sources reviewed, not a claim that the list is complete: '
        f'a missing name usually means no source was found yet. The records were last verified {esc(verified)}.</p>',

        '<h3>Curated by hand</h3>',
        f'<p>The athletics results update automatically from the schools’ websites. The alumni and endowment records '
        f'do not: they are researched and written by hand in two data files, '
        f'<a href="../../data/alumni.json">alumni.json</a> and <a href="../../data/endowment.json">endowment.json</a>, '
        f'and every change is reviewed through a pull request on <a href="{esc(repo_url)}">GitHub</a> before it '
        f'goes live.</p>',

        '<h3>Endowment per student</h3>',
        f'<p>The endowment amounts come from each school’s audited {esc(fy)} financial statements, as of '
        f'{esc(as_of)}. The two statements do not measure the same thing, so the page puts them on one basis, '
        f'{esc(basis)}. {basis_html} The page still shows the full total as a supporting line.</p>',
        f'<p>Each amount on that basis is divided by the school’s {esc(term.lower())} total undergraduate '
        f'headcount from the U.S. Department of Education’s College Navigator (IPEDS): {enrolled}. Fall '
        f'{esc(term.split()[-1])} is the academic year that ends with the {esc(as_of)} endowment date. See '
        f'{enroll_links}.</p>',
        f'<p>The result is rounded to the nearest $1,000, from the exact amounts rather than from the rounded '
        f'totals shown on the page: {figures}.</p>',
        f'<p>When the higher per-student figure is within {alumni_page.EVEN_PERCENT}% of the lower one, the page '
        f'calls them <strong>about even</strong> instead of marking one school higher. {even_html} None of the '
        f'figures is cash a school can spend freely.</p>',

        '<h3>Suggest a correction</h3>',
        f'<p>If a name, a count or a figure looks wrong or missing, use “Suggest a correction” on the alumni page '
        f'or <a href="{esc(alumni_page.correction_url())}">open the correction form</a>. Say which school, the '
        f'category and the person, and link a source. The issue is public, so please leave out personal details.</p>',
        '</section>',
    ]
