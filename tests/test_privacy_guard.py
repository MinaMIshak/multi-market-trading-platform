"""Privacy guard: platform code never embeds a personal contact and never invents the SEC contact."""
import re
from pathlib import Path

from app.context import official

ROOT = Path(__file__).parents[1]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PERSONAL_DOMAINS = {"yahoo.com", "gmail.com", "hotmail.com", "outlook.com", "live.com", "icloud.com", "aol.com",
                    "proton.me", "protonmail.com"}


def test_no_personal_mail_addresses_in_platform_code_or_tools():
    offenders = []
    for folder in ("app", "tools"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix not in (".py", ".sh", ".json", ".md", ".txt") or "__pycache__" in path.parts:
                continue
            for match in EMAIL.finditer(path.read_text(errors="ignore")):
                if match.group(1).lower() in PERSONAL_DOMAINS:
                    offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_default_user_agents_carry_no_contact_address():
    from app.context.fetch import DEFAULT_UA
    assert "@" not in DEFAULT_UA


def test_sec_contact_comes_only_from_explicit_operator_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("EGX_SEC_CONTACT", raising=False)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "person@example.org")
    monkeypatch.setenv("EMAIL", "person@example.org")
    assert official.sec_contact(path=str(tmp_path / "absent.txt")) is None
    assert official.sec_status(None) == {
        "status": "BLOCKED", "code": "NO_OPERATOR_CONTACT_EMAIL",
        "reason": official.sec_status(None)["reason"]}
