"""Ontology Loader and Validator (Contract)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, dict, set

import yaml

MODULE_DIR = Path(__file__).resolve().parent
STAGE_DIR = MODULE_DIR.parent
PROJECT_ROOT = STAGE_DIR.parent

for p in [PROJECT_ROOT, STAGE_DIR, MODULE_DIR]:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

DEFAULT_ONTOLOGY_PATH = Path(
    os.getenv("ONTOLOGY_PATH", str((MODULE_DIR / "ontology.yaml").resolve()))
).resolve()

REQUIRED_FIELDS = ["canonical", "aliases", "cwe", "subtypes", "related_terms"]

# 5 core web vulnerability pillars (used for alias-to-canonical text replacement)
CORE_VULNERABILITY_SLUGS: set[str] = {
    "bac",
    "misconfig",
    "sqli",
    "xss",
    "auth_failures",
}

# Overly short/ambiguous tokens disallowed as standalone aliases (Contract L7)
FORBIDDEN_ALIASES: set[str] = {"bac", "csp", "dos", "key", "mac", "tls"}


def load_ontology(path: str | Path = DEFAULT_ONTOLOGY_PATH) -> dict[str, dict[str, Any]]:
    """
    Load and validate the application security ontology YAML.

    Validations performed:
    1. File exists and is a valid YAML dictionary.
    2. Preserves all 5 core vulnerability domains plus any generalized supporting domains.
    3. Each entry contains all required fields (canonical, aliases, cwe, subtypes, related_terms).
    4. Aliases do not collide across different slugs (case-insensitive).
    5. Disallows overly short or ambiguous aliases (e.g., 'bac').
    """
    file_path = Path(path).resolve()
    if not file_path.is_file():
        for candidate in [MODULE_DIR / path, STAGE_DIR / path]:
            if candidate.is_file():
                file_path = candidate.resolve()
                break
        else:
            raise FileNotFoundError(f"Ontology file not found at: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or not data:
        raise ValueError("Ontology root must be a non-empty mapping/dict.")

    loaded_slugs = set(data.keys())
    if not CORE_VULNERABILITY_SLUGS.issubset(loaded_slugs):
        missing_core = CORE_VULNERABILITY_SLUGS - loaded_slugs
        raise ValueError(f"Ontology is missing required core vulnerability slugs: {missing_core}")

    alias_to_slug: dict[str, str] = {}

    for slug, entry in data.items():
        if not isinstance(slug, str) or not slug.strip():
            raise ValueError(f"Invalid ontology slug: {slug!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"Entry for slug '{slug}' must be a dictionary.")

        for field in REQUIRED_FIELDS:
            if field not in entry:
                raise ValueError(f"Slug '{slug}' is missing required field: '{field}'")

        if not isinstance(entry["canonical"], str) or not entry["canonical"].strip():
            raise ValueError(f"Slug '{slug}' field 'canonical' must be a non-empty string.")

        for list_field in ["aliases", "cwe", "subtypes", "related_terms"]:
            if not isinstance(entry[list_field], list):
                raise ValueError(f"Slug '{slug}' field '{list_field}' must be a list.")

        for alias in entry["aliases"]:
            cleaned_alias = " ".join(str(alias).strip().lower().split())
            if not cleaned_alias:
                continue

            if cleaned_alias in FORBIDDEN_ALIASES:
                raise ValueError(
                    f"Alias '{alias}' in slug '{slug}' is forbidden due to ambiguity (Contract L7)."
                )

            if cleaned_alias in alias_to_slug:
                existing_slug = alias_to_slug[cleaned_alias]
                raise ValueError(
                    f"Alias collision detected! Alias '{alias}' in slug '{slug}' "
                    f"conflicts with slug '{existing_slug}'."
                )
            alias_to_slug[cleaned_alias] = slug

    return data


def get_valid_slugs(path: str | Path = DEFAULT_ONTOLOGY_PATH) -> set[str]:
    """Return all valid domain slugs defined in ontology.yaml."""
    return set(load_ontology(path).keys())


try:
    STANDARD_VULNERABILITY_SLUGS: set[str] = get_valid_slugs()
except Exception:
    STANDARD_VULNERABILITY_SLUGS = set(CORE_VULNERABILITY_SLUGS)