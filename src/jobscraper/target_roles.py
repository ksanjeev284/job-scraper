"""Curated target-role registry for ``--role-sweep`` searches.

Each entry in ``data/target_roles.json`` pairs a display role name with
the keyword query that surfaces it on a job board, grouped under a
category (e.g. ``cybersecurity``). A sweep runs one keyword search per
role and tags every collected posting with the role that surfaced it, so
a single run covers a whole profession instead of one ad-hoc keyword.

This mirrors the ``TARGET_ROLES`` / ``scrape_target_roles`` idea from
leading open-source scrapers: the role list is curated and
de-duplicated (no role appears twice inside one category), and every
entry is a generic job title, so the registry stays profession- and
location-neutral.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from jobscraper.urls import canonicalize_url


class RoleSweepError(ValueError):
    """Raised when the role registry or a requested category is invalid."""


@dataclass(frozen=True)
class TargetRole:
    """One curated role and the keyword query used to search for it."""

    category: str
    role: str
    keywords: str


def _load_raw() -> list[dict]:
    path = resources.files("jobscraper").joinpath("data/target_roles.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoleSweepError(f"cannot read target-role registry: {exc}"
                             ) from exc


def load_roles() -> list[TargetRole]:
    """Load and validate every role in the registry."""
    raw = _load_raw()
    if not isinstance(raw, list):
        raise RoleSweepError("target-role registry must be a JSON list")
    roles: list[TargetRole] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise RoleSweepError("target-role entries must be objects")
        category = entry.get("category", "")
        role = entry.get("role", "")
        keywords = entry.get("keywords", "")
        for label, value in (("category", category), ("role", role),
                             ("keywords", keywords)):
            if not (isinstance(value, str) and value.strip()):
                raise RoleSweepError(
                    f"target-role entry is missing a non-empty '{label}'")
        key = (category.strip().lower(), role.strip().lower())
        if key in seen:
            raise RoleSweepError(
                f"target-role registry lists '{role}' twice in "
                f"category '{category}'")
        seen.add(key)
        roles.append(TargetRole(category=category.strip().lower(),
                                role=role.strip(),
                                keywords=keywords.strip()))
    return roles


def categories(roles: list[TargetRole] | None = None) -> list[str]:
    """Sorted category names present across the registry."""
    roles = roles if roles is not None else load_roles()
    return sorted({role.category for role in roles})


def roles_for(category: str,
              roles: list[TargetRole] | None = None) -> list[TargetRole]:
    """Roles in one category, in registry order.

    Raises :class:`RoleSweepError` for an unknown category.
    """
    roles = roles if roles is not None else load_roles()
    category = category.strip().lower()
    matched = [r for r in roles if r.category == category]
    if not matched:
        raise RoleSweepError(
            f"unknown role category '{category}' "
            f"(choose from: {', '.join(categories(roles))})")
    return matched


def sweep_roles(roles: list[TargetRole],
                search_fn: Callable[[str, int], list[str]],
                limit: int = 25,
                key: Callable[[str], str] = canonicalize_url
                ) -> tuple[list[str], dict[str, str],
                           list[tuple[str, int, str | None]]]:
    """Run one keyword search per role and collect the surfaced URLs.

    ``search_fn(keywords, limit)`` returns posting URLs for one keyword
    query. Returns ``(urls, url_roles, per_role)`` where ``urls`` are the
    de-duplicated URLs in sweep order, ``url_roles`` maps each URL's
    canonical key to the name of the first role that surfaced it, and
    ``per_role`` is a ``(role, added, error)`` row per role (``error`` is
    ``None`` on success). A failing role is recorded and skipped; it
    never aborts the sweep.
    """
    urls: list[str] = []
    url_roles: dict[str, str] = {}
    per_role: list[tuple[str, int, str | None]] = []
    for role in roles:
        error: str | None = None
        added = 0
        try:
            found = search_fn(role.keywords, limit) or []
        except Exception as exc:  # noqa: BLE001 - one role must not kill the sweep
            found = []
            error = str(exc)[:160] or type(exc).__name__
        for url in found:
            if not url:
                continue
            url_roles.setdefault(key(url), role.role)
            if url not in urls:
                urls.append(url)
                added += 1
        per_role.append((role.role, added, error))
    return urls, url_roles, per_role
