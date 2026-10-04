"""Tests for named saved search profiles (src/jobscraper/search_profiles.py)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from jobscraper.cli import build_parser
from jobscraper.search_profiles import (
    MANAGED_KEYS,
    ProfileError,
    default_profiles_path,
    get_profile,
    list_profile_names,
    load_profiles,
    sanitize_for_profile,
    save_search_profile,
)


@pytest.fixture
def profiles_file(tmp_path, monkeypatch):
    path = tmp_path / "search_profiles.json"
    monkeypatch.setenv("JOBSCRAPER_SEARCH_PROFILES", str(path))
    return path


def test_default_profiles_path_respects_env(monkeypatch):
    monkeypatch.setenv("JOBSCRAPER_SEARCH_PROFILES", "~/custom/profiles.json")
    path = default_profiles_path()
    assert path.name == "profiles.json"
    assert str(path).endswith("custom/profiles.json")


def test_default_profiles_path_home_dir(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_SEARCH_PROFILES", raising=False)
    assert default_profiles_path().name == "search_profiles.json"


def test_load_missing_file_returns_empty(profiles_file):
    assert load_profiles(profiles_file) == {}
    assert list_profile_names(profiles_file) == []


def test_round_trip_save_and_load(profiles_file):
    options = {"linkedin": "splunk engineer", "location": "Hyderabad",
               "limit": 30, "webhook_mode": "ntfy"}
    save_search_profile("splunk-hyd", options, profiles_file)
    assert get_profile("splunk-hyd", profiles_file) == options
    assert list_profile_names(profiles_file) == ["splunk-hyd"]


def test_save_keeps_existing_profiles(profiles_file):
    save_search_profile("a", {"limit": 10}, profiles_file)
    save_search_profile("b", {"limit": 20}, profiles_file)
    assert list_profile_names(profiles_file) == ["a", "b"]
    assert get_profile("a", profiles_file) == {"limit": 10}


def test_save_overwrites_same_name(profiles_file):
    save_search_profile("a", {"limit": 10}, profiles_file)
    save_search_profile("a", {"limit": 50}, profiles_file)
    assert get_profile("a", profiles_file) == {"limit": 50}
    assert list_profile_names(profiles_file) == ["a"]


def test_managed_keys_never_stored(profiles_file):
    options = {"linkedin": "splunk", "search_profile": "other",
               "save_search_profile": "x", "list_search_profiles": True,
               "search_profiles_file": "/tmp/y"}
    save_search_profile("clean", options, profiles_file)
    stored = get_profile("clean", profiles_file)
    assert stored == {"linkedin": "splunk"}
    assert not (set(stored) & MANAGED_KEYS)


def test_sanitize_for_profile():
    args = {"linkedin": "x", "search_profile": "p", "urls": ["http://e.com"]}
    assert sanitize_for_profile(args) == {"linkedin": "x",
                                          "urls": ["http://e.com"]}


def test_profiles_file_is_owner_only(profiles_file):
    save_search_profile("a", {"limit": 5}, profiles_file)
    mode = stat.S_IMODE(os.stat(profiles_file).st_mode)
    assert mode & 0o777 == 0o600


def test_unknown_profile_name_lists_available(profiles_file):
    save_search_profile("a", {"limit": 5}, profiles_file)
    with pytest.raises(ProfileError, match="unknown search profile 'zzz'"):
        get_profile("zzz", profiles_file)


def test_unknown_profile_name_when_empty(profiles_file):
    with pytest.raises(ProfileError, match="none saved yet"):
        get_profile("zzz", profiles_file)


def test_empty_name_rejected(profiles_file):
    with pytest.raises(ProfileError, match="must not be empty"):
        save_search_profile("   ", {"limit": 5}, profiles_file)


def test_malformed_json_raises(profiles_file):
    profiles_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match="not valid JSON"):
        load_profiles(profiles_file)


def test_non_object_json_raises(profiles_file):
    profiles_file.write_text('["a"]', encoding="utf-8")
    with pytest.raises(ProfileError, match="must contain a JSON object"):
        load_profiles(profiles_file)


def test_non_object_profile_raises(profiles_file):
    profiles_file.write_text('{"a": 5}', encoding="utf-8")
    with pytest.raises(ProfileError, match="must be a JSON object"):
        load_profiles(profiles_file)


def test_profile_applies_as_parser_defaults(profiles_file):
    """A saved profile sets defaults; CLI flags still override them."""
    save_search_profile("demo", {"limit": 40, "linkedin": "splunk",
                                 "max_age": 7}, profiles_file)
    from jobscraper.search_profiles import get_profile
    stored = get_profile("demo", profiles_file)
    parser = build_parser()
    parser.set_defaults(**stored)
    args = parser.parse_args(["--limit", "10"])
    assert args.limit == 10          # CLI flag wins
    assert args.linkedin == "splunk"  # profile default used
    assert args.max_age == 7          # profile default used


def test_parser_rejects_nonexistent_profile_option(capsys):
    """End-to-end: --search-profile with an unknown name exits 2."""
    from jobscraper.cli import main
    code = main(["--search-profile", "does-not-exist-xyz",
                 "--search-profiles-file", "/tmp/no-such-profiles-x.json"])
    assert code == 2
    assert "unknown search profile" in capsys.readouterr().err


def test_cli_save_and_list_profiles(tmp_path, capsys):
    """End-to-end: --save-search-profile writes; --list shows it."""
    from jobscraper.cli import main
    path = str(tmp_path / "sp.json")
    code = main(["--search-profiles-file", path, "--linkedin", "splunk",
                 "--limit", "25", "--save-search-profile", "nightly"])
    assert code == 0
    assert "Saved search profile 'nightly'" in capsys.readouterr().out
    stored = json.loads((tmp_path / "sp.json").read_text(encoding="utf-8"))
    assert stored["nightly"]["linkedin"] == "splunk"
    assert stored["nightly"]["limit"] == 25
    assert "save_search_profile" not in stored["nightly"]

    capsys.readouterr()
    code = main(["--search-profiles-file", path, "--list-search-profiles"])
    assert code == 0
    assert "nightly" in capsys.readouterr().out
