import re
from pathlib import Path

from desk import links

ROOT = Path(__file__).resolve().parents[2]


def _all_urls():
    return [links.coin_url("So1abc", "solana"), links.coin_url("0xabc", "robinhood"),
            links.profile_url("@someone"), links.signup_url()]


def test_ref_on_every_url(monkeypatch):
    monkeypatch.setenv("FOMO_REF", "myref")
    for u in _all_urls():
        assert "myref" in u, u
    for u in _all_urls()[:3]:
        assert "r=myref" in u, u
    assert links.signup_url().endswith("/r/myref")


def test_no_ref_when_unset(monkeypatch):
    monkeypatch.delenv("FOMO_REF", raising=False)
    for u in _all_urls():
        assert not re.search(r"[?&]r=", u) and "/r/" not in u, u


def test_unknown_chain_is_signup(monkeypatch):
    monkeypatch.setenv("FOMO_REF", "myref")
    assert links.coin_url("abc", "tron") == links.signup_url()


def test_no_hand_built_fomo_urls():
    allowed = {ROOT / "desk" / "links.py", ROOT / "copybot" / "links.py"}
    bad = []
    for ext in ("*.py", "*.js", "*.html"):
        for f in ROOT.rglob(ext):
            if f in allowed or any(part in {".git", "node_modules", "__pycache__", ".venv", "tests"}
                                   for part in f.relative_to(ROOT).parts):
                continue
            if "fomo.family" in f.read_text(errors="ignore"):
                bad.append(str(f.relative_to(ROOT)))
    assert bad == [], f"FOMO URL built outside links.py: {bad}"
