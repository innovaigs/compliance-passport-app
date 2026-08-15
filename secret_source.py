"""
Tracks where each secret came from: the process environment or a .env file.

Imported before any dotenv loading so it can snapshot the pristine process
environment. Import order matters — this module must be imported at the top of
any module that calls load_dotenv, above the dotenv import itself.

Why this exists: `.env` used to be loaded with override=True, so a value in the
file silently beat a value supplied by the deployment. Key rotation could appear
to succeed while the old key stayed in use, and a deliberately invalid key set
for a negative test was replaced by the real one, producing a false pass.
The process environment now always wins, and startup states the source so a
wrong-key incident is diagnosable in seconds.

Never logs values, prefixes, or lengths.
"""

from __future__ import annotations

import os
from typing import Dict, List

# Captured at first import, before load_dotenv has run anywhere.
_PRISTINE_ENV: Dict[str, str] = dict(os.environ)

TRACKED_SECRETS = ("ANTHROPIC_API_KEY", "DAYTONA_API_KEY")
TRACKED_SETTINGS = ("ANTHROPIC_MODEL", "COMPLIANCE_ALLOW_MOCK_SANDBOX")

ENVIRONMENT = "process environment"
DOTENV = ".env file"
UNSET = "not set"


def source_of(name: str) -> str:
    """Where the current value of `name` came from."""
    if name in _PRISTINE_ENV and _PRISTINE_ENV[name].strip():
        return ENVIRONMENT
    if os.environ.get(name, "").strip():
        return DOTENV
    return UNSET


def report_lines() -> List[str]:
    """One line per tracked variable. Names and sources only, never values."""
    lines = []
    for name in TRACKED_SECRETS + TRACKED_SETTINGS:
        source = source_of(name)
        note = ""
        if name in TRACKED_SECRETS and source == UNSET:
            note = "  (calls requiring it will raise)"
        elif source == DOTENV:
            note = "  (a value in the process environment would take precedence)"
        lines.append(f"[SECRETS] {name:32} <- {source}{note}")
    return lines


def log_report() -> None:
    for line in report_lines():
        print(line)
