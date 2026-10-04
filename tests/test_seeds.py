"""Tests for the curated seed registry (jobscraper.seeds)."""

import json
from importlib import resources

import pytest

from jobscraper import seeds
from jobscraper.boards import DISCOVERERS
from jobscraper.seeds import (
    Seed,
    SeedError,
    categories,
    discover_seeds,
    load_seeds,
    seeds_for,
)


def test_registry_loads_valid() -> None:
    all_seeds = load_seeds()
    assert len(all_seeds) >= 5
    for seed in all_seeds:
        assert seed.name and seed.name.strip()
        assert seed.board in DISCOVERERS
        assert seed.id and seed.id.strip()
        assert seed.categories
        assert seed.spec == f"{seed.board}:{seed.id}"


def test_registry_is_valid_json_package_data() -> None:
    raw = resources.files("jobscraper").joinpath("data/seeds.json")
    entries = json.loads(raw.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    specs = [f"{e['board']}:{e['id']}" for e in entries]
    assert len(specs) == len(set(specs))  # no duplicates


def test_categories_are_sorted_lowercase() -> None:
    cats = categories()
    assert cats == sorted(cats)
    assert all(c == c.lower() and c.strip() for c in cats)
    assert "tech" in cats


def test_seeds_for_none_returns_all() -> None:
    all_seeds = load_seeds()
    assert seeds_for(None) == all_seeds


def test_seeds_for_category_matches() -> None:
    matched = seeds_for("fintech")
    assert matched
    assert all("fintech" in s.categories for s in matched)
    assert seeds_for("FINTECH") == matched  # case-insensitive


def test_seeds_for_unknown_category_raises() -> None:
    with pytest.raises(SeedError, match="unknown seed category"):
        seeds_for("no-such-category")


def test_load_seeds_rejects_unknown_board(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = [{"name": "BadCo", "board": "nosuchboard", "id": "x",
            "categories": ["tech"]}]
    monkeypatch.setattr(seeds, "_load_raw", lambda: bad)
    with pytest.raises(SeedError, match="unknown board"):
        load_seeds()


def test_load_seeds_rejects_empty_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = [{"name": "BadCo", "board": "lever", "id": "x", "categories": []}]
    monkeypatch.setattr(seeds, "_load_raw", lambda: bad)
    with pytest.raises(SeedError, match="categories"):
        load_seeds()


def test_load_seeds_rejects_missing_id(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = [{"name": "BadCo", "board": "lever", "id": "",
            "categories": ["tech"]}]
    monkeypatch.setattr(seeds, "_load_raw", lambda: bad)
    with pytest.raises(SeedError, match="id"):
        load_seeds()


def test_load_seeds_rejects_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = {"name": "DupCo", "board": "lever", "id": "x",
             "categories": ["tech"]}
    monkeypatch.setattr(seeds, "_load_raw", lambda: [entry, entry])
    with pytest.raises(SeedError, match="duplicate"):
        load_seeds()


def test_discover_seeds_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(ident: str) -> list[str]:
        return [f"https://example.com/{ident}/1"]

    def boom(ident: str) -> list[str]:
        raise RuntimeError("board down")

    monkeypatch.setitem(DISCOVERERS, "lever", ok)
    monkeypatch.setitem(DISCOVERERS, "greenhouse", boom)

    seed_list = [
        Seed(name="OkCo", board="lever", id="okco",
             categories=("tech",)),
        Seed(name="BoomCo", board="greenhouse", id="boomco",
             categories=("tech",)),
    ]
    results = discover_seeds(seed_list)
    assert results == {
        "lever:okco": ["https://example.com/okco/1"],
        "greenhouse:boomco": [],
    }


def test_list_seeds_cli(capsys: pytest.CaptureFixture[str]) -> None:
    from jobscraper.cli import main
    assert main(["--list-seeds"]) == 0
    out = capsys.readouterr().out
    assert "Spotify" in out and "lever:spotify" in out
    assert "categories:" in out


def test_discover_seeds_bad_category_cli() -> None:
    from jobscraper.cli import main
    assert main(["--discover-seeds", "no-such-category"]) == 2


def test_discover_seeds_cli_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    from jobscraper.cli import main

    def fake_ok(ident: str) -> list[str]:
        return [f"https://careers.example/{ident}/1"]

    for board in ("lever", "ashby", "greenhouse", "teamtailor"):
        monkeypatch.setitem(DISCOVERERS, board, fake_ok)

    seen: list[str] = []
    from jobscraper import cli as cli_mod

    def fake_run(urls: list[str], **kwargs) -> tuple:
        seen.extend(urls)
        assert kwargs.get("run_stats") is True
        return ([], 0, 0, [])

    monkeypatch.setattr(cli_mod, "run_pipeline", fake_run)
    # Sweep only the ai category so the sweep stays tiny.
    main(["--discover-seeds", "ai", "--keyword-filter", "zzz-no-match",
          "--out", "/tmp/seeds-smoke.json", "--md", "/tmp/seeds-smoke.md",
          "--csv", "/tmp/seeds-smoke.csv"])
    assert any("/careers.example/" in u for u in seen), \
        f"no seeded URLs reached the pipeline: {seen!r}"
