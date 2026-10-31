"""
The proportional record bar shared by the home page, the sport cards and the alumni page.

Widths are whole percents that sum to exactly 100, written as `.w-0` to `.w-100` classes (never inline styles,
which the CSP forbids). The bar is decorative (aria-hidden): the numbers next to it say the same in text.
"""

from __future__ import annotations


def record_widths(counts: list) -> list:
    """Whole-percent bar widths for counts, summing to exactly 100 (largest remainder; a zero count gets 0 and a
    nonzero count is never rounded away entirely unless it is under 0.5%). All zeros when there are no games."""
    total = sum(counts)
    if not total:
        return [0] * len(counts)
    exact = [c * 100 / total for c in counts]
    widths = [int(x) for x in exact]
    order = sorted(range(len(counts)), key=lambda i: (exact[i] - widths[i], counts[i]), reverse=True)
    for i in order[:100 - sum(widths)]:
        widths[i] += 1
    return widths


def record_bar(depauw: int, ties: int, wabash: int, css_class: str = "") -> str:
    """The proportional record bar: DePauw, ties, Wabash. Widths are the .w-N classes (never inline styles, which
    the CSP forbids). Decorative: the numbers beside it say the same in text."""
    widths = record_widths([depauw, ties, wabash])
    segs = "".join(f'<span class="seg {cls} w-{w}"></span>'
                   for cls, w, n in zip(("seg-d", "seg-t", "seg-w"), widths, (depauw, ties, wabash)) if n)
    return f'<div class="rbar {css_class}" aria-hidden="true">{segs}</div>'.replace('class="rbar "', 'class="rbar"')
