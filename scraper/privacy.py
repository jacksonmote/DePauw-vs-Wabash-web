"""
The privacy policy page (/privacy/): what the site does and does not do with visitors' information, in plain
language. render.py supplies the page shell (header, footer, head tags); this module holds the wording.

The policy describes what the site actually does, and tests/test_privacy.py keeps it true: no cookies, no
storage, no network request from the one script, no other script, no resource from another origin, and no
personal data in any issue link. Adding a script, analytics, cookies, storage, an embed or any third-party
request means changing this text, UPDATED and those tests in the same pull request.

UPDATED is a constant, not the build date: it moves only when the policy text changes (set it to the day the
change merges). The sitemap's lastmod for the page comes from it. PRIVACY_EMAIL is the one email address on the
whole site: it appears on this page only (never in the footer, and never in security.txt, which lists no email
address). SOURCE_PATH is this file's own path in the repository; the "Changes" links are built from it and a
test checks that it exists.
"""

from __future__ import annotations

import datetime as dt
import html

UPDATED = "2026-11-01"           # the day the policy text last changed
PATH = "privacy/"                # relative to the site root
SOURCE_PATH = "scraper/privacy.py"                    # this file, in the repository
PRIVACY_EMAIL = "privacy@motetechnologygroup.com"     # the privacy contact: privacy page only
TITLE = "Privacy | DePauw vs. Wabash"
H2 = "Privacy policy"
DESCRIPTION = ("No cookies, analytics, ads or trackers, and no personal information collected or sold. "
               "What this site does, and what GitHub does as its host.")

GITHUB_PRIVACY = "https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement"
GITHUB_TERMS = "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service"
GITHUB_PAGES_DATA = ("https://docs.github.com/en/pages/getting-started-with-github-pages/"
                     "what-is-github-pages#data-collection")
GITHUB_SUBPROCESSORS = "https://docs.github.com/en/site-policy/privacy-policies/github-subprocessors"

# What "public information" means for the alumni entries. The privacy page and /how-it-works/alumni/ both use
# this text, so the two say the same thing.
PUBLIC_INFO = ("information already published by the schools, government bodies, award organizations or news "
               "outlets, with a link to each source next to each entry. The site never publishes contact "
               "details, home addresses or other private information about anyone.")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def updated_text(iso: str = UPDATED) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def _a(href: str, label: str) -> str:
    return f'<a href="{esc(href)}">{esc(label)}</a>'


def source_urls(repo_url: str) -> tuple:
    """(history, source): the commits view and the file view of SOURCE_PATH on main, for the Changes section."""
    return f"{repo_url}/commits/main/{SOURCE_PATH}", f"{repo_url}/blob/main/{SOURCE_PATH}"


def content(*, repo_url: str, correction_url: str) -> list:
    """The page body: one prose section. The URLs come from render.py (the issue-form link the footer uses)."""
    security = repo_url + "/security/advisories/new"
    history, source = source_urls(repo_url)
    mail = f'<a href="mailto:{esc(PRIVACY_EMAIL)}">{esc(PRIVACY_EMAIL)}</a>'
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
        '<p>The site is hosted on GitHub Pages. When you visit, GitHub, and the service providers it uses to run '
        'its services, receive your IP address and the details of the request, such as its date and time and your '
        'device information. GitHub states that a Pages visitor’s IP address is logged and stored for security '
        'purposes, whether or not the visitor is signed in to GitHub (see the “Data collection” section of '
        f'{_a(GITHUB_PAGES_DATA, "What is GitHub Pages?")}). The people who run this site don’t see or receive '
        'those logs.</p>',
        f'<p>GitHub’s {_a(GITHUB_PRIVACY, "General Privacy Statement")} explains how it handles this information, '
        f'and its {_a(GITHUB_SUBPROCESSORS, "subprocessors list")} names the service providers it uses.</p>',
        '<p>This site doesn’t add any service of its own between you and GitHub Pages: its address points '
        'directly at GitHub.</p>',
        '<h3>Corrections, suggestions and security reports</h3>',
        '<p>The correction and suggestion links at the bottom of each page open a form on GitHub. You need a '
        'GitHub account to use one, and what you submit becomes a public GitHub issue that anyone can read and '
        'that is tied to your GitHub username. Please don’t post anything personal, such as phone numbers, email '
        'addresses or home addresses. The links themselves carry only the page, category or sport you were '
        'looking at, nothing about you.</p>',
        f'<p>A security problem can be reported privately instead, through '
        f'{_a(security, "GitHub’s private vulnerability reporting")}. '
        'Those reports are visible only to the site’s maintainers and GitHub. '
        f'Everything you do on GitHub is covered by {_a(GITHUB_TERMS, "GitHub’s Terms of Service")} and '
        f'{_a(GITHUB_PRIVACY, "Privacy Statement")}.</p>',
        '<h3>The data on the site</h3>',
        '<p>Everything shown here is public information: game results from the two schools’ own athletics '
        'pages, alumni and endowment figures from public sources linked next to each entry, and the Monon Bell '
        'heist history and tiger facts, each with a source. On a schedule, an automated process reads the '
        'schools’ public athletics pages to update the records. It collects nothing about visitors, and the '
        'data it stores is sports data only.</p>',
        f'<p>Here, public information means {esc(PUBLIC_INFO)}</p>',
        '<p>Alumni entries are limited to public roles and public sources: offices people held, awards they '
        'received, and degrees confirmed by their school or another published source. If you’re listed and want '
        f'your entry reviewed or removed, email {mail} (privately) or use the '
        f'{_a(correction_url, "correction form")} on GitHub (publicly). A short, general request is enough, with '
        'no personal details needed, and we’ll consider it.</p>',
        '<h3>Links to other sites</h3>',
        '<p>Pages here link to other sites, including the schools’ athletics and college sites, Wikipedia, news '
        'outlets, government sites and College Navigator. Those sites have their own privacy policies, and '
        'this one doesn’t cover them.</p>',
        '<h3>Changes</h3>',
        '<p>If this policy changes, the “Last updated” date at the top changes with it. The full history is in '
        f'the public repository: {_a(history, "changes to this page")} and {_a(source, "the page’s source")}.</p>',
        '<h3>Contact</h3>',
        f'<p>Questions about this policy or a privacy request (including about an alumni entry): {mail}.</p>',
        '<p>This is an unofficial fan site, not affiliated with DePauw University or Wabash College.</p>',
        '</section>',
    ]
