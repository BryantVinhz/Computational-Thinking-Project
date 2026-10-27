"""Query normalization between validation and LLM understanding.

Phase A cleans whitespace and Unicode. Phase B maps ontology aliases to canonical names.
"""

import functools
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# Chars and sequences that make a query look like code or an attack payload.
# Percent and hex escapes need exactly two hex digits so "100%" and "50% off" do not match.
PAYLOAD_LIKE_PATTERN = re.compile(r"[<>'\"=;()`]|--|/\*|%[0-9A-Fa-f]{2}|\\x[0-9A-Fa-f]{2}")

ZERO_WIDTH_PATTERN = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")

# Any whitespace except "\n" (spaces, tabs, NBSP, ideographic space, ...)
HORIZONTAL_WHITESPACE_PATTERN = re.compile(r"[^\S\n]+")

EXCESS_NEWLINES_PATTERN = re.compile(r"\n{3,}")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_conservative(text: str) -> str:
    return _normalize_newlines(text).strip()


def _normalize_regular(text: str) -> str:
    text = _normalize_newlines(text)
    text = ZERO_WIDTH_PATTERN.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = HORIZONTAL_WHITESPACE_PATTERN.sub(" ", text)
    text = "\n".join(line.strip(" ") for line in text.split("\n"))
    text = EXCESS_NEWLINES_PATTERN.sub("\n\n", text)
    # Zero-width-only input yields ""; the pipeline must handle an empty normalized_query.
    return text.strip()


def _alias_key(text: str) -> str:
    text = unicodedata.normalize("NFC", text.casefold())
    return HORIZONTAL_WHITESPACE_PATTERN.sub(" ", text).strip()


def _alternation(keys) -> str:
    # Longest first so longer aliases and protected phrases win over their parts.
    ordered = sorted(set(keys), key=lambda k: (-len(k), k))
    body = "|".join(r"[^\S\n]+".join(map(re.escape, k.split(" "))) for k in ordered)
    return rf"(?<![\w-])(?:{body})(?![\w-])"


def _str_list(entry: dict, field: str, slug: str) -> tuple[str, ...]:
    value = entry.get(field, [])
    if not isinstance(value, list):
        logger.warning("Ontology slug %r: field %r is not a list, ignored", slug, field)
        return ()
    items = tuple(sorted(item for item in value if isinstance(item, str)))
    if len(items) != len(value):
        logger.warning("Ontology slug %r: non-string items in %r ignored", slug, field)
    return items


def _ontology_entries(ontology: dict) -> tuple:
    """Return valid (slug, canonical, aliases, terms) tuples sorted by slug."""
    entries = []
    for slug, entry in ontology.items():
        if not isinstance(slug, str) or not isinstance(entry, dict):
            logger.warning("Ontology entry %r is not a str -> dict mapping, ignored", slug)
            continue
        canonical = entry.get("canonical")
        if not isinstance(canonical, str) or not _alias_key(canonical):
            logger.warning("Ontology slug %r has no valid canonical, ignored", slug)
            continue
        terms = _str_list(entry, "subtypes", slug) + _str_list(entry, "related_terms", slug)
        entries.append((slug, canonical, _str_list(entry, "aliases", slug), tuple(sorted(terms))))
    return tuple(sorted(entries))


@functools.lru_cache(maxsize=16)
def _build_matcher(entries: tuple) -> tuple[re.Pattern | None, dict[str, str | None]]:
    """Compile the alias regex and its lookup table. None values mark protected phrases."""
    owners: dict[str, set[str]] = {}
    canonicals: dict[str, str] = {}
    for slug, canonical, aliases, _ in entries:
        canonicals[slug] = " ".join(unicodedata.normalize("NFC", canonical).split())
        for alias in (canonical, *aliases):
            key = _alias_key(alias)
            if key:
                owners.setdefault(key, set()).add(slug)

    table: dict[str, str | None] = {}
    for key, slugs in owners.items():
        if len(slugs) > 1:
            logger.warning("Ontology alias %r is shared by slugs %s, ignored", key, sorted(slugs))
            continue
        table[key] = canonicals[next(iter(slugs))]

    # Subtypes/related terms that contain another slug's alias map to themselves.
    for slug, _, _, terms in entries:
        foreign = [key for key, slugs in owners.items() if slug not in slugs]
        if not foreign:
            continue
        foreign_pattern = re.compile(_alternation(foreign), re.IGNORECASE)
        for term in terms:
            key = _alias_key(term)
            if key and key not in owners and foreign_pattern.search(key):
                table[key] = None

    if not table:
        return None, table
    return re.compile(_alternation(table), re.IGNORECASE), table


def _apply_aliases(text: str, ontology: dict) -> str:
    pattern, table = _build_matcher(_ontology_entries(ontology))
    if pattern is None:
        return text
    return pattern.sub(lambda m: table.get(_alias_key(m.group())) or m.group(), text)


def normalize_query(raw_query: str, ontology: dict | None = None) -> dict:
    """
    Normalize a validated query without changing its meaning.

    Returns a dict with exactly 3 keys:
    - original_query: raw_query unchanged
    - normalized_query: conservatively trimmed if payload-like, otherwise fully
      normalized with ontology aliases replaced by their canonical names
    - payload_like: True if raw_query contains code or payload-like characters
    """
    if not isinstance(raw_query, str):
        raise TypeError(f"raw_query must be str, got {type(raw_query).__name__}")

    # Detect payload patterns after removing zero-width chars so they cannot split multi-char patterns.
    payload_like = bool(PAYLOAD_LIKE_PATTERN.search(ZERO_WIDTH_PATTERN.sub("", raw_query)))
    if payload_like:
        normalized = _normalize_conservative(raw_query)
    else:
        normalized = _normalize_regular(raw_query)

    # Phase B is skipped for payload-like queries, so their aliases stay as typed.
    if not payload_like and normalized and isinstance(ontology, dict) and ontology:
        normalized = _apply_aliases(normalized, ontology)
    elif ontology is not None and not isinstance(ontology, dict):
        logger.warning("Ontology must be a dict, got %s; alias mapping skipped", type(ontology).__name__)

    return {
        "original_query": raw_query,
        "normalized_query": normalized,
        "payload_like": payload_like,
    }
