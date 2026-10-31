"""/.well-known/security.txt and SECURITY.md: one private reporting address, a valid RFC 9116 file that never goes
stale, and the deploy step that lets a dot folder reach the site. Fixed clock; reads repo files only."""
import datetime as dt
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
from test_render import NOW, build, football  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ADVISORY = render.REPO_URL + "/security/advisories/new"


def fields(text):
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            name, value = line.split(": ", 1)
            out.setdefault(name, []).append(value)
    return out


def test_the_file_has_the_rfc_9116_fields_and_points_at_private_reporting():
    text = render.render_security_txt(NOW)
    assert text.endswith("\n") and "\r" not in text
    f = fields(text)
    assert f["Contact"] == [ADVISORY]
    assert f["Canonical"] == [render.SITE_URL + ".well-known/security.txt"]
    assert f["Policy"] == [render.REPO_URL + "/blob/main/SECURITY.md"]
    assert f["Preferred-Languages"] == ["en"] and len(f["Expires"]) == 1
    assert "@" not in text and "mailto" not in text.lower()                     # no email address, ever
    assert all(re.fullmatch(r"[A-Za-z-]+: \S.*|#.*", line) for line in text.splitlines())


def test_expires_is_under_a_year_ahead_whenever_the_site_is_built_and_is_deterministic():
    for now in (NOW, dt.datetime(2027, 1, 1, 5, 0, tzinfo=dt.timezone.utc), dt.datetime(2028, 2, 29, 23, 59, tzinfo=dt.timezone.utc)):
        expires = dt.datetime.fromisoformat(fields(render.render_security_txt(now))["Expires"][0].replace("Z", "+00:00"))
        assert now < expires < now + dt.timedelta(days=366)
        assert render.render_security_txt(now) == render.render_security_txt(now)
    # the date is the build day in UTC, so two builds on one day write the same file
    a = dt.datetime(2026, 11, 14, 1, 0, tzinfo=dt.timezone.utc)
    b = dt.datetime(2026, 11, 14, 23, 0, tzinfo=dt.timezone.utc)
    assert render.render_security_txt(a) == render.render_security_txt(b)


def test_the_build_writes_it_and_the_only_dot_entries_in_the_site_are_the_two_expected(tmp_path):
    out, _ = build(tmp_path, {"football": football()})
    assert (out / ".well-known" / "security.txt").read_text(encoding="utf-8") == render.render_security_txt(NOW)
    dots = sorted(p.name for p in out.rglob("*") if p.name.startswith("."))
    assert dots == [".nojekyll", ".well-known"]                                   # the deploy now publishes dot files: keep this list exact


def test_it_is_not_listed_in_the_sitemap_or_robots(tmp_path):
    out, _ = build(tmp_path, {"football": football()})
    assert "security.txt" not in (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "security.txt" not in (out / "robots.txt").read_text(encoding="utf-8")


def test_the_deploy_upload_includes_hidden_files():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "update-and-deploy.yml").read_text(encoding="utf-8"))
    upload = next(s for s in wf["jobs"]["build"]["steps"] if s.get("uses", "").startswith("actions/upload-pages-artifact@"))
    assert upload["with"] == {"path": "site", "include-hidden-files": True}


def test_security_md_gives_the_same_private_address_and_nothing_private():
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert ADVISORY in text
    assert "@" not in re.sub(r"https?://\S+", "", text)                            # no email address
    for banned in ("LLC", "Mote Technology"):
        assert banned not in text
    assert "not affiliated" in text and "Report a data error" in text                  # says what it is, and where wrong scores go
