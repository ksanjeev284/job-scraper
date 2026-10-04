"""Tests for the curated target-role registry and --role-sweep logic."""

import json
from importlib import resources

import pytest

from jobscraper import target_roles
from jobscraper.models import Posting
from jobscraper.reporting import (
    _spreadsheet_headers,
    _spreadsheet_row,
    write_markdown,
    write_rss,
)
from jobscraper.target_roles import (
    RoleSweepError,
    TargetRole,
    categories,
    load_roles,
    roles_for,
    sweep_roles,
)


def _sample_roles() -> list[TargetRole]:
    return [
        TargetRole(category="cybersecurity", role="SOC Analyst",
                   keywords="SOC analyst"),
        TargetRole(category="cybersecurity", role="SIEM Engineer",
                   keywords="SIEM engineer"),
        TargetRole(category="design", role="UX Designer",
                   keywords="UX designer"),
    ]


def test_registry_loads_valid() -> None:
    roles = load_roles()
    assert len(roles) >= 50
    for role in roles:
        assert role.category and role.category == role.category.lower()
        assert role.role and role.role.strip()
        assert role.keywords and role.keywords.strip()
    # de-duplicated within a category
    keys = [(r.category, r.role.lower()) for r in roles]
    assert len(keys) == len(set(keys))


def test_registry_is_valid_json_package_data() -> None:
    raw = resources.files("jobscraper").joinpath("data/target_roles.json")
    entries = json.loads(raw.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    assert {e["category"] for e in entries} >= {
        "software_engineering", "cybersecurity", "data_science"}


def test_categories_sorted() -> None:
    cats = categories()
    assert cats == sorted(cats)
    assert "cybersecurity" in cats


def test_roles_for_known_category_in_registry_order() -> None:
    roles = roles_for("cybersecurity")
    assert len(roles) >= 5
    assert all(r.category == "cybersecurity" for r in roles)
    names = [r.role for r in roles]
    assert "SOC Analyst" in names
    assert "SIEM Engineer" in names
    # registry order is preserved (not re-sorted)
    all_roles = load_roles()
    expected = [r.role for r in all_roles if r.category == "cybersecurity"]
    assert names == expected


def test_roles_for_unknown_category_lists_valid() -> None:
    with pytest.raises(RoleSweepError) as excinfo:
        roles_for("astronaut")
    assert "cybersecurity" in str(excinfo.value)


def test_load_roles_rejects_non_list(monkeypatch) -> None:
    monkeypatch.setattr(target_roles, "_load_raw", lambda: {"x": 1})
    with pytest.raises(RoleSweepError):
        load_roles()


def test_load_roles_rejects_non_dict_entry(monkeypatch) -> None:
    monkeypatch.setattr(target_roles, "_load_raw", lambda: ["nope"])
    with pytest.raises(RoleSweepError):
        load_roles()


def test_load_roles_rejects_empty_fields(monkeypatch) -> None:
    bad = [{"category": "x", "role": "  ", "keywords": "y"}]
    monkeypatch.setattr(target_roles, "_load_raw", lambda: bad)
    with pytest.raises(RoleSweepError):
        load_roles()


def test_load_roles_rejects_duplicate_role_in_category(monkeypatch) -> None:
    bad = [
        {"category": "x", "role": "Analyst", "keywords": "analyst"},
        {"category": "x", "role": "analyst", "keywords": "analyst jobs"},
    ]
    monkeypatch.setattr(target_roles, "_load_raw", lambda: bad)
    with pytest.raises(RoleSweepError):
        load_roles()


def test_load_roles_allows_same_role_in_other_category(monkeypatch) -> None:
    ok = [
        {"category": "x", "role": "Analyst", "keywords": "analyst"},
        {"category": "y", "role": "Analyst", "keywords": "analyst"},
    ]
    monkeypatch.setattr(target_roles, "_load_raw", lambda: ok)
    assert len(load_roles()) == 2


def _fake_search(mapping: dict[str, list[str]],
                 failures: set[str] | None = None):
    def search_fn(keywords: str, limit: int) -> list[str]:
        if failures and keywords in failures:
            raise RuntimeError("board exploded")
        return [u for u in mapping.get(keywords, [])][:limit]
    return search_fn


def test_sweep_roles_collects_and_tags_first_role_wins() -> None:
    mapping = {
        "SOC analyst": ["https://a.example/1", "https://a.example/2"],
        "SIEM engineer": ["https://a.example/2", "https://a.example/3"],
        "UX designer": [],
    }
    urls, url_roles, per_role = sweep_roles(
        _sample_roles(), _fake_search(mapping), limit=10,
        key=lambda u: u)
    assert urls == ["https://a.example/1", "https://a.example/2",
                    "https://a.example/3"]
    # the URL surfaced by two roles keeps the first one
    assert url_roles["https://a.example/2"] == "SOC Analyst"
    assert url_roles["https://a.example/3"] == "SIEM Engineer"
    assert per_role == [("SOC Analyst", 2, None),
                        ("SIEM Engineer", 1, None),
                        ("UX Designer", 0, None)]


def test_sweep_roles_respects_limit() -> None:
    mapping = {"SOC analyst": [f"https://a.example/{i}" for i in range(5)]}
    urls, _, per_role = sweep_roles([_sample_roles()[0]],
                                    _fake_search(mapping), limit=3,
                                    key=lambda u: u)
    assert len(urls) == 3
    assert per_role[0] == ("SOC Analyst", 3, None)


def test_sweep_roles_records_role_failure_and_continues() -> None:
    mapping = {"SIEM engineer": ["https://a.example/9"]}
    urls, url_roles, per_role = sweep_roles(
        _sample_roles(), _fake_search(mapping, {"SOC analyst"}),
        limit=10, key=lambda u: u)
    assert per_role[0][0] == "SOC Analyst"
    assert per_role[0][1] == 0
    assert per_role[0][2] and "exploded" in per_role[0][2]
    assert urls == ["https://a.example/9"]
    assert url_roles["https://a.example/9"] == "SIEM Engineer"


def test_sweep_roles_uses_canonical_key() -> None:
    mapping = {"SOC analyst": ["HTTPS://A.EXAMPLE/1?utm_source=x#frag"]}
    urls, url_roles, _ = sweep_roles([_sample_roles()[0]],
                                     _fake_search(mapping), limit=10)
    # default key is canonicalize_url: one entry, canonical key
    assert len(urls) == 1
    assert len(url_roles) == 1
    canon = next(iter(url_roles))
    assert canon == "https://a.example/1"
    assert url_roles[canon] == "SOC Analyst"


def test_posting_search_role_default_and_serialization() -> None:
    post = Posting(url="https://example.com/job/1")
    assert post.search_role is None
    assert post.to_dict()["search_role"] is None
    post.search_role = "SOC Analyst"
    assert post.to_dict()["search_role"] == "SOC Analyst"


def test_spreadsheet_columns_carry_search_role() -> None:
    headers = _spreadsheet_headers()
    assert "search_role" in headers
    assert headers.index("search_role") == len(headers) - 2  # before url
    post = Posting(url="https://example.com/job/1", title="SOC Analyst",
                   company="Acme", search_role="SOC Analyst")
    row = _spreadsheet_row(post, {})
    assert row[headers.index("search_role")] == "SOC Analyst"
    blank = Posting(url="https://example.com/job/2")
    assert _spreadsheet_row(blank, {})[headers.index("search_role")] == ""


def test_markdown_and_rss_carry_search_role(tmp_path) -> None:
    from jobscraper.models import MatchResult
    post = Posting(url="https://example.com/job/1", title="SOC Analyst",
                   company="Acme", search_role="SOC Analyst",
                   match=MatchResult(total=80))
    md = tmp_path / "r.md"
    write_markdown([post], md, {})
    assert "- Search role: SOC Analyst" in md.read_text(encoding="utf-8")
    rss = tmp_path / "r.xml"
    write_rss([post], rss, {})
    assert "<category>role:SOC Analyst</category>" in rss.read_text(
        encoding="utf-8")
