"""Named saved search profiles for jobscraper.

A search profile is a named snapshot of the CLI's full option set (sources,
filters, exports, notification settings, ...).  Profiles are stored as JSON
in ``~/.jobscraper/search_profiles.json`` (or the path in the
``JOBSCRAPER_SEARCH_PROFILES`` environment variable / ``--search-profiles-file``).

Running ``jobscraper --search-profile NAME ...`` loads the profile as the
defaults for every option, so any flag still given on the command line
overrides the saved value.  ``jobscraper --save-search-profile NAME ...``
stores the current invocation as a profile; ``--list-search-profiles`` shows
what is stored.

Profiles may contain notification credentials (ntfy/pushover/telegram/SMTP
settings) when the user passed them on the command line, so the file is
written with mode 0600.  It is a local file and is never shipped with the
repo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PROFILES_FILENAME = "search_profiles.json"
ENV_VAR = "JOBSCRAPER_SEARCH_PROFILES"

# Options managed by the profiles feature itself; never stored inside one.
MANAGED_KEYS = frozenset({
    "search_profile",
    "save_search_profile",
    "list_search_profiles",
    "search_profiles_file",
})


class ProfileError(Exception):
    """Raised when a search profile cannot be loaded or saved."""


def default_profiles_path() -> Path:
    """Resolve the JSON file profiles are stored in."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path("~/.jobscraper").expanduser() / DEFAULT_PROFILES_FILENAME


def load_profiles(path: Path | str | None = None) -> dict[str, dict]:
    """Load all saved search profiles, keyed by name.

    A missing file yields ``{}``; a malformed file raises :class:`ProfileError`.
    """
    resolved = Path(path).expanduser() if path else default_profiles_path()
    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ProfileError(f"cannot read profiles file {resolved}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileError(
            f"profiles file {resolved} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(
            f"profiles file {resolved} must contain a JSON object of profiles")
    for name, options in data.items():
        if not isinstance(options, dict):
            raise ProfileError(
                f"profile '{name}' in {resolved} must be a JSON object")
    return data


def save_profiles(profiles: dict[str, dict],
                  path: Path | str | None = None) -> Path:
    """Persist all profiles; the file is created with mode 0600."""
    resolved = Path(path).expanduser() if path else default_profiles_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                           0o600), "w", encoding="utf-8") as fh:
            json.dump(profiles, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        raise ProfileError(f"cannot write profiles file {resolved}: {exc}"
                           ) from exc
    try:
        os.chmod(resolved, 0o600)
    except OSError:
        pass
    return resolved


def list_profile_names(path: Path | str | None = None) -> list[str]:
    """Return the sorted names of all saved search profiles."""
    return sorted(load_profiles(path))


def get_profile(name: str, path: Path | str | None = None) -> dict:
    """Return the stored options for ``name``.

    Raises :class:`ProfileError` for an unknown name, listing what exists.
    """
    profiles = load_profiles(path)
    if name not in profiles:
        known = ", ".join(sorted(profiles)) or "(none saved yet)"
        raise ProfileError(f"unknown search profile '{name}' "
                           f"(saved profiles: {known})")
    return dict(profiles[name])


def save_search_profile(name: str, options: dict,
                        path: Path | str | None = None) -> Path:
    """Store ``options`` under ``name``; existing profiles are kept."""
    if not name or not name.strip():
        raise ProfileError("profile name must not be empty")
    profiles = load_profiles(path)
    clean = {key: value for key, value in options.items()
             if key not in MANAGED_KEYS}
    profiles[name.strip()] = clean
    return save_profiles(profiles, path)


def sanitize_for_profile(options: dict) -> dict:
    """Drop the profile-management keys from a parsed-args dict."""
    return {key: value for key, value in options.items()
            if key not in MANAGED_KEYS}
