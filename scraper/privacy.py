"""
The privacy policy page (/privacy/): what the site does and does not do with visitors' information, in plain
language. render.py supplies the page shell (header, footer, head tags); this module holds the wording.

The policy describes what the site actually does, and tests/test_privacy.py keeps it true: no cookies, no
storage, no network request from the one script, no other script, no resource from another origin, and no
personal data in any issue link. Adding a script, analytics, cookies, storage, an embed or any third-party
request means changing this text, UPDATED and those tests in the same pull request.

UPDATED is a constant, not the build date: it moves only when the policy text changes. The sitemap's lastmod
for the page comes from it.
"""

from __future__ import annotations

import datetime as dt
import html

UPDATED = "2026-10-31"           # the day the policy text last changed; update it with any change to this file
PATH = "privacy/"                # relative to the site root
TITLE = "Privacy | DePauw vs. Wabash"
H2 = "Privacy policy"
DESCRIPTION = ("No cookies, analytics, ads or trackers, and no personal information collected or sold. "
               "What this site does, and what GitHub does as its host.")

GITHUB_PRIVACY = "https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement"
GITHUB_TERMS = "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service"
GITHUB_PAGES_DOCS = "https://docs.github.com/en/pages"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def updated_text(iso: str = UPDATED) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def _a(href: str, label: str) -> str:
    return f'<a href="{esc(href)}">{esc(label)}</a>'


def content(*, repo_url: str, report_url: str, correction_url: str, enhancement_url: str) -> list:
    """The page body: one prose section. The URLs come from render.py (the issue-form links the footer uses)."""
    security = repo_url + "/security/advisories/new"
    history = repo_url + "/commits/main/scraper/privacy.py"
    source = repo_url + "/blob/main/scraper/privacy.py"
    return [
        '<section class="prose privacy" aria-labelledby="privacy-h">',
        f'<h2 id="privacy-h">{esc(H2)}</h2>',
        f'<p class="updated">Last updated <time datetime="{esc(UPDATED)}">{esc(updated_text())}</time></p>',
        '<h3>The short version</h3>',
        '<p>This site doesn’t collect, store or sell any personal information. It sets no cookies and uses no '
        'analytics, ads or trackers. Every font, image and style comes from this site itself, so showing a page '
        'doesn’t make your browser contact any other site.</p>',
        '<p>Two pages (the home page and the alumni page) run a small script, hosted on this site, for a couple '
        'of easter eggs. It stores nothing and sends nothing anywhere.</p>',
        '<h3>Hosting and GitHub’s logs</h3>',
        f'<p>The site is hosted on {_a(GITHUB_PAGES_DOCS, "GitHub Pages")}, which serves it through GitHub’s own '
        'content-delivery network. Like any web host, GitHub receives your IP address and the details of each '
        'request, such as the page you asked for and your browser type, and may keep them in its server logs. '
        f'That is covered by {_a(GITHUB_PRIVACY, "GitHub’s Privacy Statement")}. The people who run this site '
        'don’t see or receive those logs.</p>',
        '<p>The site’s address, depauwvswabash.com, points directly at GitHub Pages. No other service sits '
        'between you and GitHub.</p>',
        '<h3>Corrections, suggestions and security reports</h3>',
        f'<p>The links “{_a(report_url, "Report a data error")},” “{_a(correction_url, "Suggest a correction")}” '
        f'and “{_a(enhancement_url, "Suggest an enhancement")}” open a form on GitHub. You need a GitHub account '
        'to use one, and what you submit becomes a public GitHub issue that anyone can read and that is tied to '
        'your GitHub username. Please don’t post anything personal, such as phone numbers, email addresses or '
        'home addresses. The links themselves carry only the page, category or sport you were looking at, '
        'nothing about you.</p>',
        f'<p>A security problem can be reported privately instead, through {_a(security, "GitHub’s private vulnerability reporting")}. '
        'Those reports are visible only to the site’s maintainers and GitHub. '
        f'Everything you do on GitHub is covered by {_a(GITHUB_TERMS, "GitHub’s Terms of Service")} and '
        f'{_a(GITHUB_PRIVACY, "Privacy Statement")}.</p>',
        '<h3>The data on the site</h3>',
        '<p>Everything shown here is public information: game results from the two schools’ own athletics '
        'pages, alumni and endowment figures from public sources linked next to each entry, and the Monon Bell '
        'heist history and tiger facts, each with a source. On a schedule, an automated process reads the '
        'schools’ public athletics pages to update the records. It collects nothing about visitors, and the '
        'data it stores is sports data only.</p>',
        f'<p>If you’re listed on the alumni page and something about your entry is wrong, you can ask for a '
        f'correction through the {_a(correction_url, "correction form")}.</p>',
        '<h3>Links to other sites</h3>',
        '<p>Pages here link to other sites, including the schools’ athletics and college sites, Wikipedia, news '
        'outlets, government sites and College Navigator. Those sites have their own privacy policies, and '
        'this one doesn’t cover them.</p>',
        '<h3>Children</h3>',
        '<p>The site collects no information from anyone, so there is nothing specific to children.</p>',
        '<h3>Changes</h3>',
        f'<p>If this policy changes, the “Last updated” date at the top changes with it. The full history is in '
        f'the public repository: {_a(history, "changes to this page")} and {_a(source, "the page’s source")}.</p>',
        '<h3>Contact</h3>',
        f'<p>This site has no email address, contact form or accounts. To reach the people who run it, use the '
        f'{_a(enhancement_url, "enhancement form")} on GitHub, or the private security link above for a '
        'security problem.</p>',
        '<p>This is an unofficial fan site, not affiliated with DePauw University or Wabash College, and this page '
        'describes the facts about the site. It isn’t legal advice.</p>',
        '</section>',
    ]
