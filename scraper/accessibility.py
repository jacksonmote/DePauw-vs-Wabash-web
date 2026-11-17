"""
The accessibility page (/accessibility/): what the site aims for (WCAG 2.2 level AA), what was tested and how,
what works, what is known not to, and how to report a problem, in plain language. render.py supplies the page
shell (header, footer, head tags); this module holds the wording.

The page describes what the site actually does, and tests/test_accessibility.py keeps it true: the contrast of
every color pair, the page structure, text alternatives and link names. It says "aims to meet", never "conforms"
or "certified": nobody outside the project has audited the site. A fix, a new limitation or a new round of
testing means changing this text and UPDATED in the same pull request (REVIEWED too, when the pages are reviewed
again). A page added after the last full review goes into ADDED_PAGES with the day it was checked, and the page
says so, so "we reviewed every page" stays true; tests/test_accessibility.py fails for a page in neither list.

UPDATED is a constant, not the build date: it moves only when the page's text changes (set it to the day the
change merges). The sitemap's lastmod for the page comes from it. ACCESSIBILITY_EMAIL is the accessibility
contact: it appears on this page only, as the privacy contact appears only on the privacy page (never in the
footer, and never in security.txt, which lists no email address). The thresholds and widths below are the ones
the tests and axe/run.js use, and a test checks they still match. SOURCE_PATH is this file's own path in the
repository; the "Changes" links are built from it and a test checks that it exists.
"""

from __future__ import annotations

import html

import belltimeline
import privacy

UPDATED = "2026-11-17"           # the day the page's text last changed
REVIEWED = "2026-11-15"          # the day of the last full review of every page
# Pages added since that review: (path from the site root, the page's name, the day it was checked). Each was
# checked the day it was added with the tools the sentence below names; the next full review empties this list.
ADDED_PAGES = ((belltimeline.PATH, belltimeline.TITLE, "2026-11-17"),)
PATH = "accessibility/"          # relative to the site root
SOURCE_PATH = "scraper/accessibility.py"              # this file, in the repository
ACCESSIBILITY_EMAIL = "accessibility@motetechnologygroup.com"   # the accessibility contact: this page only
TITLE = "Accessibility | DePauw vs. Wabash"
H2 = "Accessibility"
DESCRIPTION = ("How this site aims to meet WCAG 2.2 level AA: what was tested, what works, known limitations, "
               "and how to report an accessibility problem.")

WCAG_VERSION, WCAG_LEVEL = "2.2", "AA"
WCAG_URL = "https://www.w3.org/TR/WCAG22/"
AXE_URL = "https://github.com/dequelabs/axe-core"
TEXT_MIN, NON_TEXT_MIN = 4.5, 3      # contrast: normal-size text (WCAG 1.4.3); fills, edges, and focus rings (1.4.11)
PHONE_WIDTHS, DESKTOP_WIDTH = (320, 375), 1280      # CSS pixels: the widths every page is checked at
ZOOM_PERCENT = 200


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _a(href: str, label: str) -> str:
    return f'<a href="{esc(href)}">{esc(label)}</a>'


def source_urls(repo_url: str) -> tuple:
    """(history, source): the commits view and the file view of SOURCE_PATH on main, for the Changes section."""
    return f"{repo_url}/commits/main/{SOURCE_PATH}", f"{repo_url}/blob/main/{SOURCE_PATH}"


def content(*, repo_url: str, suggestion_url: str) -> list:
    """The page body: one prose section. `suggestion_url` is the enhancement issue form the footer links to."""
    history, source = source_urls(repo_url)
    mail = f'<a href="mailto:{esc(ACCESSIBILITY_EMAIL)}">{esc(ACCESSIBILITY_EMAIL)}</a>'
    wcag = f"WCAG {WCAG_VERSION}"
    narrow, phone = PHONE_WIDTHS
    reviewed = privacy.updated_text(REVIEWED)
    return [
        '<section class="prose accessibility" aria-labelledby="accessibility-h">',
        f'<h2 id="accessibility-h">{esc(H2)}</h2>',
        f'<p class="updated">Last updated <time datetime="{esc(UPDATED)}">{esc(privacy.updated_text(UPDATED))}</time></p>',
        '<h3>Our goal</h3>',
        '<p>We want everyone to be able to read these records, whatever device or assistive technology they use. '
        f'This site aims to meet the {_a(WCAG_URL, f"Web Content Accessibility Guidelines (WCAG) {WCAG_VERSION}")} '
        f'at level {WCAG_LEVEL}. That is a goal we work toward, not a claim that every page meets every '
        'requirement: nobody outside the project has audited the site, and it holds no certification.</p>',
        '<h3>What we did</h3>',
        f'<p>On <time datetime="{esc(REVIEWED)}">{esc(reviewed)}</time> we reviewed every page of the site against '
        f'{wcag} level {WCAG_LEVEL}, in light and dark mode, at phone widths ({narrow} and {phone} pixels) and at '
        'desktop width. The review covered:</p>',
        '<ul>',
        f'<li>every page checked with {_a(AXE_URL, "axe-core")}, an open-source accessibility testing tool;</li>',
        '<li>the contrast of every text and background color pair in both modes, including the gold and scarlet '
        'school colors;</li>',
        '<li>every page used with a keyboard alone: the order of focus, the focus ring, the skip link, and each '
        'section that opens and closes;</li>',
        f'<li>zoom to {ZOOM_PERCENT}%, narrow screens, wider text spacing, reduced motion, and high-contrast '
        '(forced colors) mode;</li>',
        '<li>how the browser describes each page to assistive technology: headings, landmarks, tables, images, '
        'charts, and link names.</li>',
        '</ul>',
        '<p>We fixed what we found, apart from the items under “Known limitations” below.</p>',
        *[f'<p>On <time datetime="{esc(day)}">{esc(privacy.updated_text(day))}</time> we added a page, '
          f'{_a("../" + path, name)}, and checked it with the same tools: axe-core, the contrast of its colors, '
          f'a keyboard alone, zoom to {ZOOM_PERCENT}%, and wider text spacing.</p>'
          for path, name, day in ADDED_PAGES],
        '<p>Some checks now run on every change to the site. A test works out the contrast of every color pair in '
        f'both modes, and nothing is published if one falls below the {wcag} level {WCAG_LEVEL} minimums '
        f'({TEXT_MIN}:1 for text, {NON_TEXT_MIN}:1 for bars, edges, and focus rings). Other tests check page '
        'structure, text alternatives, and link names. axe-core runs on every page whenever a change is proposed.</p>',
        '<h3>What we haven’t tested</h3>',
        '<p>We haven’t tested with a real screen reader (NVDA, JAWS, VoiceOver, or TalkBack), in Safari, or on '
        'physical phones. The review was done in the Chromium browser on a computer, using its phone-width and '
        'accessibility tools. Automated tools catch only some problems, so there may be barriers we haven’t '
        'found. If you meet one, please tell us (see “Report a problem” below).</p>',
        '<h3>What works</h3>',
        '<ul>',
        '<li><strong>Keyboard.</strong> Everything can be reached and used with a keyboard alone. A “Skip to '
        'content” link is the first stop on every page, focus always shows as a thick ring, and nothing traps '
        'it.</li>',
        '<li><strong>Screen readers.</strong> Each page has one main heading, headings in order, and header, '
        'navigation, main, and footer landmarks. Tables have captions and labelled rows and columns. Each chart '
        'has a text description, and the full list of games is on the same card, in one table per decade that you can '
        'open and close. Decorative images are hidden from assistive technology.</li>',
        '<li><strong>Reduced motion.</strong> The site has almost no motion. If your device asks for reduced '
        'motion, the little there is gets switched off.</li>',
        '<li><strong>Light and dark mode.</strong> The site follows your device’s setting, and both modes are '
        'checked for contrast. Nothing relies on color alone: every result is also written out in words and numbers.</li>',
        f'<li><strong>Zoom and small screens.</strong> Pages work at {ZOOM_PERCENT}% zoom and on screens {narrow} '
        'pixels wide without scrolling sideways. Zoom is never switched off.</li>',
        '<li><strong>No script needed.</strong> Every page works with JavaScript off. Nothing moves, plays, or '
        'times out by itself.</li>',
        '</ul>',
        '<h3>Known limitations</h3>',
        '<ul>',
        '<li><strong>Easter eggs.</strong> The home and alumni pages hide a few optional jokes. One is an '
        'invisible button over the site title: a keyboard user sees a focus ring around the title with no visible '
        'label, and a screen reader announces a button called “Tiger entrance” with a short hint. The home page '
        'also has a small picture link to a joke page and a tiger with a random fact. Each one works with a '
        'keyboard and has a name a screen reader can read, and none is needed to use the site. The paw prints are '
        'decoration and are hidden from assistive technology.</li>',
        f'<li><strong>The scoreboard with wider text spacing.</strong> On a screen {narrow} pixels wide, if you '
        'apply your own wider letter and word spacing, the large numbers on the home page’s scoreboard crowd '
        'together and a sliver of space at the edge is cut off. Every digit stays readable.</li>',
        '<li><strong>Game lists on phones.</strong> On narrow screens each game is shown as two short lines '
        'instead of a table row, and the column names are hidden from view. Underneath it is still a table with '
        'those column names, and the Chromium browser describes it as one, but we haven’t confirmed that every '
        'screen reader announces it as a table at that size. Each result starts with W, L, or T (a DePauw win, '
        'loss, or tie) and names the winner in words.</li>',
        '<li><strong>Charts.</strong> Each every-game chart is summed up in a text description, not read out bar '
        'by bar. The full results are in the list of all games on the same card.</li>',
        '</ul>',
        '<h3>Content we don’t control</h3>',
        '<p>Pages here link to the two schools’ athletics sites (history pages, schedules, and box scores), to '
        'GitHub, and to the sources cited for alumni, endowment, heist, and tiger-fact entries, some of which are '
        'PDF documents. We don’t control those sites and can’t promise they are accessible. The correction and '
        'suggestion forms are on GitHub and need a GitHub account; if that is a barrier, email us instead.</p>',
        '<h3>Report a problem</h3>',
        '<p>If something on this site is hard to use, or you can’t get to some information, please tell us:</p>',
        '<ul>',
        f'<li>email {mail} (private), or</li>',
        f'<li>use the {_a(suggestion_url, "suggestion form on GitHub")} (public, and it needs a GitHub account).</li>',
        '</ul>',
        '<p>It helps to say which page, what you were trying to do, and the browser and any assistive technology '
        'you use. We’ll respond, and we’ll fix what we can.</p>',
        '<h3>Changes</h3>',
        '<p>When this page changes, the “Last updated” date at the top changes with it. The full history is in '
        f'the public repository: {_a(history, "changes to this page")} and {_a(source, "the page’s source")}.</p>',
        '<p>This is an unofficial fan site, not affiliated with DePauw University or Wabash College.</p>',
        '</section>',
    ]
