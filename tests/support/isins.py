"""Real and correctly-structured-synthetic ISINs for tests.

``stock.isin`` is UNIQUE NOT NULL, so a test that inserts a stock must supply a
distinct, valid-format ISIN — not filler. A uniqueness constraint satisfied by
``"AAA"`` is a latent bug, and a round-trip test that passes on junk identity is
worse than no test. So:

- ``REAL_ISINS`` are genuine NSE ISINs (correct ISO 6166 check digit).
- ``synthetic_isin(seed)`` builds a deterministic, unique, **check-digit-valid**
  ISIN for call-sites that create many stocks (e.g. per-test fixtures) — the
  check digit is computed the same way a real ISIN's is.

SEEDING CONVENTION (structural, not a "remember to" rule):
``synthetic_isin`` is the DEFAULT for all test seeding. The test DB schema is
append-only and immutable, so any ``REAL_ISINS`` constant is held for the WHOLE
suite run — two tests seeding the same entry collide on the ``isin`` UNIQUE
constraint. ``REAL_ISINS`` is therefore reserved for tests whose PURPOSE is a
real ISIN (ISO-6166 / check-digit format validation); for plain seeding, always
use ``synthetic_isin(<unique-seed>)``.

Residual pre-existing ``REAL_ISINS`` seeds (Phase-1/2a, left as-is — they pass and
are out of this phase's scope): ``tests/reproducibility/test_reproducibility.py``
(INFY, TCS) and the ``scripts/phase0_e2e.py`` demo (INFY). New tests must not add
to this surface.
"""

from __future__ import annotations

import hashlib
import string

# Genuine NSE ISINs (verified ISO 6166 check digits).
REAL_ISINS: dict[str, str] = {
    "RELIANCE": "INE002A01018",
    "INFY": "INE009A01021",
    "TCS": "INE467B01029",
    "HDFCBANK": "INE040A01034",
    "ICICIBANK": "INE090A01021",
    "SBIN": "INE062A01020",
}

_ALNUM = string.digits + string.ascii_uppercase


def _check_digit(body11: str) -> str:
    """ISO 6166 check digit: letters -> 2-digit values, then Luhn (mod-10)."""
    converted = "".join(
        str(ord(c) - 55) if c.isalpha() else c for c in body11
    )
    total = 0
    for i, ch in enumerate(reversed(converted)):
        d = int(ch)
        if i % 2 == 0:  # double every second digit from the right of the body
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - (total % 10)) % 10)


def make_isin(body11: str) -> str:
    """Append the correct check digit to an 11-char ISIN body."""
    body11 = body11.upper()
    if len(body11) != 11:
        raise ValueError(f"ISIN body must be 11 chars, got {body11!r}")
    return body11 + _check_digit(body11)


def synthetic_isin(seed: str) -> str:
    """A deterministic, check-digit-valid ISIN for ``seed`` (country code ``IN``).

    Distinct seeds yield distinct ISINs, so call-sites that create many stocks
    (e.g. ``f"IMMUT_{uuid4}"``) stay unique without colliding on the constraint.
    """
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest().upper()
    nsin = "".join(c for c in digest if c in _ALNUM)[:9].ljust(9, "0")
    return make_isin("IN" + nsin)
