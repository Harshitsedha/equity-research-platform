"""The code version stamped onto every ledger artifact.

Reproducibility (ADR-003) requires that everything affecting an output is
version-stamped. ``CODE_VERSION`` resolves to the actual git commit (short SHA,
plus a ``-dirty`` suffix if the working tree has uncommitted changes) so the
stamp genuinely identifies the code that produced a snapshot.

Purity note (ADR-004): this reads the SHA via ``subprocess`` — stdlib only, with
**no third-party import and no architectural-layer dependency** — so the domain
stays pure. It is resolved exactly ONCE, at import time, never per call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: Used when git is unavailable, this is not a checkout, or there are no commits.
_FALLBACK = "unknown-dev"

# Run git from this package's directory so it finds the right repository
# regardless of the process's current working directory.
_REPO_DIR = Path(__file__).resolve().parent


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=_REPO_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _resolve_code_version() -> str:
    sha = _git("rev-parse", "--short", "HEAD")
    if not sha:
        return _FALLBACK
    # A non-empty porcelain status means the working tree differs from HEAD,
    # so the stamp must not claim to match the commit cleanly.
    status = _git("status", "--porcelain")
    if status:
        return f"{sha}-dirty"
    return sha


#: Stamped into Snapshot.code_version and ValuationRun.code_version. Resolved once.
CODE_VERSION: str = _resolve_code_version()
