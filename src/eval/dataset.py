"""Test-set loader for the eval harness.

Today's only test set is `data/test-sets/v0.1-mini.md` — a 10-sentence
Turkish edge-case battery (numbers, abbreviations, code-mix, prosody).
The format is markdown with a single table; we parse it instead of
duplicating the corpus in code so a domain expert can edit the markdown
without touching Python.

Future test sets (`v1.0-full.md`, etc.) drop in the same directory and
register here by filename. We deliberately do NOT auto-discover new
files — every test set used in a benchmark needs an explicit pinned
slug so the report header can record "this score was computed against
v0.1-mini" rather than "whatever was on disk at the time."
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestSentence:
    """One row of a test set. `index` is the position in the source
    markdown (used to print human-readable references in the report),
    not a stable global id."""

    index: int
    category: str
    text: str


@dataclass(frozen=True)
class TestSet:
    """A registered test set. `slug` is the value reports record so a
    benchmark's identity is stable across markdown edits to the test
    sentences themselves (the slug is the pinned identifier; the
    contents can be revised within a slug as long as the count + intent
    stay equivalent)."""

    slug: str
    path: Path
    sentences: tuple[TestSentence, ...]


_REGISTERED: dict[str, Path] = {
    # Pinned slug → markdown path. New entries here only — never
    # auto-discovery, see module docstring.
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _register_default_sets() -> None:
    """Wire the bundled test sets at import time. New sets go here, not
    in a config file — drift discipline."""
    root = _repo_root()
    candidates = {
        "v0.1-mini": root / "data" / "test-sets" / "v0.1-mini.md",
    }
    for slug, path in candidates.items():
        if path.exists():
            _REGISTERED[slug] = path


_register_default_sets()


_TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<idx>\d+)\s*\|\s*(?P<cat>[^|]+?)\s*\|\s*(?P<txt>[^|]+?)\s*\|\s*$"
)


def _parse_table(md: str) -> tuple[TestSentence, ...]:
    """Lift sentences out of the markdown table.

    The file format is the standard pipe-table used by the v0.1-mini
    file: `| # | Kategori | Metin |` with one row per sentence. We
    skip header + separator rows by requiring the first column to be
    an integer.
    """
    rows: list[TestSentence] = []
    for line in md.splitlines():
        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue
        rows.append(
            TestSentence(
                index=int(m.group("idx")),
                category=m.group("cat").strip(),
                text=m.group("txt").strip(),
            )
        )
    return tuple(rows)


def load_test_set(slug: str) -> TestSet:
    """Load a registered test set by slug. Raises KeyError on unknown
    slugs — the call-site MUST be explicit; we never silently fall back
    to a default set so a benchmark's identity stays unambiguous."""
    if slug not in _REGISTERED:
        raise KeyError(
            f"test set slug '{slug}' not registered. "
            f"Known slugs: {sorted(_REGISTERED)}"
        )
    path = _REGISTERED[slug]
    md = path.read_text(encoding="utf-8")
    sentences = _parse_table(md)
    if not sentences:
        raise ValueError(
            f"test set '{slug}' at {path} contains no parseable sentences"
        )
    return TestSet(slug=slug, path=path, sentences=sentences)


def list_test_sets() -> list[str]:
    """Slugs of every registered test set. CLI surface for users to
    pick a set without grepping the source."""
    return sorted(_REGISTERED)
