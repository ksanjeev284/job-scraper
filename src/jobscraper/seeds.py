"""Curated registry of verified company career boards for --discover sweeps.

Each entry in ``data/seeds.json`` names a company, the ATS board its career
page is served by, the board-specific identifier accepted by ``--discover``,
and one or more category tags. Every entry was verified to enumerate at
least one open posting when the registry was added; boards change over
time, so a stale entry simply yields no postings and never raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from jobscraper.boards import DISCOVERERS


class SeedError(ValueError):
    """Raised when the seed registry or a requested category is invalid."""


@dataclass(frozen=True)
class Seed:
    """A verified company career board usable with ``--discover``."""

    name: str
    board: str
    id: str
    categories: tuple[str, ...]

    @property
    def spec(self) -> str:
        """The ``BOARD:ID`` string accepted by ``--discover``."""
        return f"{self.board}:{self.id}"


def _load_raw() -> list[dict]:
    path = resources.files("jobscraper").joinpath("data/seeds.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SeedError(f"cannot read seed registry: {exc}") from exc


def load_seeds() -> list[Seed]:
    """Load and validate every seed in the registry."""
    raw = _load_raw()
    if not isinstance(raw, list):
        raise SeedError("seed registry must be a JSON list")
    seeds: list[Seed] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise SeedError("seed registry entries must be objects")
        name = entry.get("name", "")
        board = entry.get("board", "")
        ident = entry.get("id", "")
        cats = entry.get("categories", [])
        if not (isinstance(name, str) and name.strip()):
            raise SeedError("seed entry is missing a non-empty 'name'")
        if board not in DISCOVERERS:
            raise SeedError(
                f"seed '{name}': unknown board '{board}' "
                f"(known: {', '.join(sorted(DISCOVERERS))})"
            )
        if not (isinstance(ident, str) and ident.strip()):
            raise SeedError(f"seed '{name}': missing a non-empty 'id'")
        if (
            not isinstance(cats, list)
            or not cats
            or any(not isinstance(c, str) or not c.strip() for c in cats)
        ):
            raise SeedError(f"seed '{name}': 'categories' must be a "
                            "non-empty list of strings")
        spec = f"{board}:{ident}"
        if spec in seen:
            raise SeedError(f"seed registry contains a duplicate '{spec}'")
        seen.add(spec)
        seeds.append(Seed(name=name.strip(), board=board,
                          id=ident.strip(),
                          categories=tuple(c.strip().lower() for c in cats)))
    return seeds


def categories(seeds: list[Seed] | None = None) -> list[str]:
    """Sorted category tags present across the registry."""
    seeds = seeds if seeds is not None else load_seeds()
    return sorted({cat for seed in seeds for cat in seed.categories})


def seeds_for(category: str | None = None,
              seeds: list[Seed] | None = None) -> list[Seed]:
    """Seeds for one category, or every seed when ``category`` is None.

    Raises :class:`SeedError` for an unknown category.
    """
    seeds = seeds if seeds is not None else load_seeds()
    if category is None:
        return seeds
    category = category.strip().lower()
    matched = [s for s in seeds if category in s.categories]
    if not matched:
        raise SeedError(
            f"unknown seed category '{category}' "
            f"(choose from: {', '.join(categories(seeds))})"
        )
    return matched


def discover_seeds(seeds: list[Seed]) -> dict[str, list[str]]:
    """Enumerate postings for each seed; returns ``{spec: urls}``.

    A seed that fails or yields nothing contributes an empty list; errors
    never abort the sweep.
    """
    results: dict[str, list[str]] = {}
    for seed in seeds:
        try:
            found = DISCOVERERS[seed.board](seed.id)
            results[seed.spec] = list(found)
        except Exception:
            results[seed.spec] = []
    return results
