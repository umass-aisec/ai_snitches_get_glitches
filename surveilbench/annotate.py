"""Derive each scenario's person and write it into that scenario's metadata.json.

The identity block tells the agent whose machine it is deployed on (see
:func:`surveilbench.prompts.build_identity`). The dataset does not ship that name
as a field, so it is parsed out of the documents **once**, here, and stored as
``person_name`` — the run path only reads metadata.

Two sources, in order:

1. The ``*_AIChatLog_*`` document — the human whose assistant session the packet
   captured. Its session-metadata block names them in one of several shapes::

       **User:** Felix Kim <felix.kim@asterionmedical.com>   (HF dataset)
       User: Felix Kim <felix.kim@asterionmedical.com>       (bundled sample_data)
       **User:** Nisha Patel
       **User account:** taylor.bennett@oakridgecommerce.com
       Account: miles.grant@portcamden.gov

2. Failing that, the ``*_EmailThread_*`` document's **first** ``**From:**``
   line — the person who opens the thread::

       **From:** Talia Kim <talia.kim@cedarridgeuniversity.edu>
       **From:** Marisol Vega, Communications, Black Cedar Police Department

Bare addresses become names from the local part (``felix.kim`` -> ``Felix Kim``),
and a trailing role clause after a comma is dropped.

Run it with ``surveilbench annotate``; ``surveilbench download`` also runs it, so
a freshly downloaded dataset is annotated on arrival.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PERSON_KEY = "person_name"
# ``person_name`` is written immediately above this key, so the two identity
# fields sit together in metadata.json.
PERSON_ANCHOR_KEY = "institution_name"
# Written by an earlier version of this module; dropped on re-annotation.
_LEGACY_SOURCE_KEY = "person_name_source"

_CHATLOG_GLOB = "*AIChatLog*.md"
_EMAILTHREAD_GLOB = "*EmailThread*.md"

# The asterisks are optional because the two corpora differ: the HF dataset
# bolds session-metadata labels (``**User:**``, ``**From:**``) and the older
# bundled sample_data did not (``User:``). Accepts ``User:`` and
# ``User account:``, with the colon inside or outside the bold.
_USER_FIELD_RE = re.compile(
    r"^\*{0,2}User(?: account)?\*{0,2}:\*{0,2}[ \t]+(\S[^\n]*?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
# An inline ``Account: <address>``, anywhere on a line.
_ACCOUNT_RE = re.compile(r"\bAccount:[ \t]*(?P<email>[^\s<>@]+@[^\s<>,;]+)")
# Searched, not iterated: only the first ``From:`` line counts.
_FROM_RE = re.compile(r"^\*{0,2}From:\*{0,2}[ \t]+(\S[^\n]*?)[ \t]*$", re.MULTILINE)

_NAME_WITH_EMAIL_RE = re.compile(r"^(?P<name>[^<>@]+?)\s*<[^\s<>@]+@[^\s<>]+>$")
_BARE_EMAIL_RE = re.compile(r"^(?P<local>[^\s<>@]+)@[^\s<>@]+$")


def _name_from_email_local(local: str) -> str | None:
    """``felix.kim`` -> ``Felix Kim``."""
    parts = [p for p in re.split(r"[._\-+]+", local) if p]
    return " ".join(p.capitalize() for p in parts) or None


def _person_from_field(raw: str) -> str | None:
    """Turn one ``User:`` / ``From:`` value into a bare person name."""
    raw = raw.strip()
    match = _NAME_WITH_EMAIL_RE.match(raw)
    if match:
        raw = match.group("name").strip()
    else:
        match = _BARE_EMAIL_RE.match(raw)
        if match:
            return _name_from_email_local(match.group("local"))
    # "Marisol Vega, Communications, ..." -> "Marisol Vega".
    raw = raw.split(",", 1)[0].strip()
    if not raw or "@" in raw or "<" in raw or not re.search(r"[A-Za-z]", raw):
        return None
    return raw if 1 <= len(raw.split()) <= 4 else None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _from_chat_log(scenario_dir: Path) -> str | None:
    for path in sorted(scenario_dir.glob(_CHATLOG_GLOB)):
        text = _read(path)
        for raw in _USER_FIELD_RE.findall(text):
            name = _person_from_field(raw)
            if name:
                return name
        match = _ACCOUNT_RE.search(text)
        if match:
            name = _name_from_email_local(match.group("email").split("@", 1)[0])
            if name:
                return name
    return None


def _from_email_thread(scenario_dir: Path) -> str | None:
    for path in sorted(scenario_dir.glob(_EMAILTHREAD_GLOB)):
        match = _FROM_RE.search(_read(path))
        if match:
            name = _person_from_field(match.group(1))
            if name:
                return name
    return None


def extract_person_name(scenario_dir: str | Path) -> tuple[str | None, str | None]:
    """Return ``(person_name, source)`` for one scenario directory.

    ``source`` is ``"chat_log"``, ``"email_thread"``, or None when neither
    document names anyone.
    """
    scenario_dir = Path(scenario_dir)
    name = _from_chat_log(scenario_dir)
    if name:
        return name, "chat_log"
    name = _from_email_thread(scenario_dir)
    if name:
        return name, "email_thread"
    return None, None


def _with_person_name(metadata: dict[str, Any], name: str | None) -> dict[str, Any]:
    """Reinsert ``person_name`` just above ``institution_name``, dropping strays.

    Rebuilt rather than assigned so the key lands in the same place on every run,
    whatever position (if any) it already held.
    """
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if key in (PERSON_KEY, _LEGACY_SOURCE_KEY):
            continue
        if key == PERSON_ANCHOR_KEY and name:
            out[PERSON_KEY] = name
        out[key] = value
    if name and PERSON_KEY not in out:
        out[PERSON_KEY] = name
    return out


def annotate_scenario(scenario_dir: str | Path, overwrite: bool = True) -> str | None:
    """Write ``person_name`` into one scenario's metadata.json; return the name."""
    scenario_dir = Path(scenario_dir)
    metadata_path = scenario_dir / "metadata.json"
    if not metadata_path.exists():
        return None
    metadata = json.loads(_read(metadata_path))
    if not overwrite and metadata.get(PERSON_KEY):
        return str(metadata[PERSON_KEY])

    name, _source = extract_person_name(scenario_dir)
    metadata_path.write_text(
        json.dumps(_with_person_name(metadata, name), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return name


def annotate_dataset(root: str | Path, overwrite: bool = True) -> dict[str, Any]:
    """Annotate every scenario under a dataset root. Returns a per-source tally.

    Both globs are needed: the main split sits at ``<axis>/<scenario>/`` and the
    benign control split one level deeper at ``benign/<axis>/<scenario>/``.
    ``sorted(set(...))`` because a three-level root would otherwise be walked
    twice by the two patterns.
    """
    root = Path(root)
    stats: dict[str, Any] = {"scenarios": 0, "chat_log": 0, "email_thread": 0, "unresolved": []}
    metadata_paths = sorted(
        set(root.glob("*/*/metadata.json")) | set(root.glob("*/*/*/metadata.json"))
    )
    for metadata_path in metadata_paths:
        scenario_dir = metadata_path.parent
        stats["scenarios"] += 1
        name, source = extract_person_name(scenario_dir)
        annotate_scenario(scenario_dir, overwrite=overwrite)
        if source:
            stats[source] += 1
        else:
            stats["unresolved"].append(
                str(scenario_dir.relative_to(root)) if scenario_dir.is_relative_to(root)
                else scenario_dir.name
            )
    return stats
