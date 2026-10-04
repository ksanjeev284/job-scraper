"""Salary threshold parsing and filtering.

Applies ``--min-salary`` / ``--max-salary`` filters to the normalized
salary figures produced by :func:`jobscraper.extract.normalize_salary`
(each entry carries ``currency``, ``min_annual`` and ``max_annual``).

Design notes:

- A threshold is an annual amount plus a currency, e.g. ``"80K USD"``,
  ``"$120k"``, ``"25 LPA"`` (lakh / LPA count as INR), ``"1.5M INR"``.
  The currency may be omitted (``"80k"``); the threshold then matches
  figures in any currency, documented as approximate.
- ``--min-salary`` keeps a posting when the *top* of any matching
  salary range reaches the threshold (the posting could plausibly pay
  that much). ``--max-salary`` keeps it when the *bottom* of any
  matching range is at or below the threshold.
- Postings with no parsed salary figures are kept: a missing salary is
  an explicit unknown, not evidence the pay is too low or too high.
- Errored postings are kept so failures stay visible.
"""

from __future__ import annotations

import re

CURRENCIES = ("INR", "EUR", "USD", "GBP")

_LPA_MULTIPLIER = 100_000

_SYMBOL_TO_CURRENCY = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
}

_WORD_TO_CURRENCY = {
    "inr": "INR",
    "lpa": "INR",
    "lakh": "INR",
    "lakhs": "INR",
    "usd": "USD",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "gbp": "GBP",
}

_THRESHOLD_RE = re.compile(
    r"^\s*"
    r"(?P<symbol>[$€£₹])?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<suffix>[kKmM])?\s*"
    r"(?P<word>[A-Za-z]+)?"
    r"\s*$"
)


def parse_salary_threshold(spec: str) -> tuple[int, str | None]:
    """Parse a salary threshold like ``"80K USD"`` or ``"25 LPA"``.

    Returns ``(annual_amount, currency)`` where ``currency`` is one of
    ``CURRENCIES`` or ``None`` when no currency was given. Raises
    :class:`ValueError` for unparseable specs or conflicting
    currencies (e.g. ``"$100 EUR"``).
    """
    if not spec or not spec.strip():
        raise ValueError("empty salary threshold")
    match = _THRESHOLD_RE.match(spec)
    if not match:
        raise ValueError(
            f"unparseable salary threshold: {spec!r} "
            "(expected e.g. '80K USD', '$120k', '25 LPA', '1.5M INR')"
        )
    word = (match.group("word") or "").lower()
    word_currency = _WORD_TO_CURRENCY.get(word)
    if word and word_currency is None:
        raise ValueError(
            f"unknown currency {word!r} in {spec!r} "
            f"(valid: {', '.join(CURRENCIES)}, LPA/lakh)"
        )
    symbol = match.group("symbol") or ""
    symbol_currency = _SYMBOL_TO_CURRENCY.get(symbol)
    if symbol_currency and word_currency and symbol_currency != word_currency:
        raise ValueError(
            f"conflicting currencies in {spec!r} "
            f"({symbol_currency} vs {word_currency})"
        )
    currency = word_currency or symbol_currency

    amount = float(match.group("amount").replace(",", ""))
    suffix = (match.group("suffix") or "").lower()
    if suffix == "k":
        amount *= 1_000
    elif suffix == "m":
        amount *= 1_000_000
    elif word in ("lpa", "lakh", "lakhs"):
        amount *= _LPA_MULTIPLIER
    if amount <= 0:
        raise ValueError(f"salary threshold must be positive: {spec!r}")
    return int(amount), currency


def meets_salary_threshold(posting, min_threshold: tuple[int, str | None] | None,
                           max_threshold: tuple[int, str | None] | None) -> bool:
    """Check one posting against parsed salary thresholds.

    Each threshold is the ``(amount, currency)`` tuple from
    :func:`parse_salary_threshold`. ``None`` means that side is unset.
    ``min_threshold`` keeps postings whose range *top* reaches the
    amount; ``max_threshold`` keeps postings whose range *bottom* is at
    or below the amount.
    """
    if getattr(posting, "error", None):
        return True
    figures = getattr(posting, "salary_normalized", None) or []
    if not figures:
        return True  # unknown salary: keep, never silently drop

    def passes(amount: int, currency: str | None, is_min: bool) -> bool:
        relevant = [f for f in figures
                    if currency is None or f.get("currency") == currency]
        if not relevant:
            return False
        if is_min:
            return any(f.get("max_annual", 0) >= amount for f in relevant)
        return any(f.get("min_annual", float("inf")) <= amount
                   for f in relevant)

    if min_threshold is not None:
        amount, currency = min_threshold
        if not passes(amount, currency, is_min=True):
            return False
    if max_threshold is not None:
        amount, currency = max_threshold
        if not passes(amount, currency, is_min=False):
            return False
    return True
