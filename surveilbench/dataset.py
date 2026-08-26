"""Load SurveilBench scenarios from a dataset root directory.

A dataset root looks like::

    surveilbench/
      corporate/scenario_001/ ... scenario_101/
      educational/scenario_001/ ...
      police/scenario_001/ ...
      benign/corporate/scenario_001/ ...    # the benign control split

Each scenario folder holds 11 Markdown documents (5 core + 5 routine
distractors + ``A1_Contact_Matrix.md``) and a ``metadata.json``.

``benign/`` mirrors the axis layout one level deeper and is loaded **only** when
``benign=True`` is asked for. Each benign scenario is the *same* scenario as the
main-split one with its ``axis``/``scenario_id`` — same organization, same
people, same contact matrix — rewritten so that nothing in the packet warrants
escalation. The two splits therefore share every id, and mixing them in one run
would collide on it.

To match the evaluation harness, document paths shown to the agent are
anonymized (``doc_<hash>.md``) and the five "core" documents are ordered to
appear after the distractors (see :func:`surveilbench.env` / the agent runner).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from .types import Document, Scenario, normalize_severity_band

AXES = ("corporate", "educational", "police")

#: Sub-directory of the dataset root holding the benign control split, and the
#: value written into ``Scenario.metadata["split"]`` for its scenarios.
BENIGN_DIR = "benign"
BENIGN_SPLIT = "benign"
MAIN_SPLIT = "main"


def _read_md(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_hidden_rel_path(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts if part and part != "/")


def _is_hidden_dir(path: Path) -> bool:
    return path.name.startswith(".")


def _anon_doc_path(rel_path: Path) -> str:
    digest = hashlib.md5(rel_path.as_posix().encode("utf-8")).hexdigest()[:10]
    return f"doc_{digest}.md"


def _normalized_axis(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().lower() or None


def _parse_selector(token: str) -> tuple[str | None, str]:
    """Parse one ``--scenario`` token into ``(axis or None, scenario_id)``.

    Accepts ``scenario_042``, the axis-qualified ``corporate/scenario_042``, and
    the bare number ``42``. The id repeats across axes, so qualifying it is the
    only way to name exactly one scenario in a full dataset.
    """
    token = token.strip().lower()
    axis: str | None = None
    if "/" in token:
        axis_part, token = token.split("/", 1)
        axis = axis_part.strip() or None
        token = token.strip()
    if token.isdigit():
        token = f"scenario_{int(token):03d}"
    return axis, token


def _parse_selectors(scenario_ids: str | Iterable[str] | None) -> list[tuple[str | None, str]] | None:
    """Normalize the ``scenario_ids`` argument into selector pairs."""
    if scenario_ids is None:
        return None
    if isinstance(scenario_ids, str):
        tokens = scenario_ids.split(",")
    else:
        tokens = [t for item in scenario_ids for t in str(item).split(",")]
    selectors = [_parse_selector(t) for t in tokens if t.strip()]
    return selectors or None


def _surveilbench_is_distractor(rel_path: Path, metadata: dict[str, Any]) -> bool:
    """Documents at index >= 5 in the metadata ``files`` list, plus the contact
    matrix, are non-core ("distractor") for ordering purposes."""
    ordered_files = [str(item) for item in metadata.get("files") or []]
    rel_path_str = rel_path.as_posix()
    if rel_path_str in ordered_files:
        return ordered_files.index(rel_path_str) >= 5
    if rel_path.name in ordered_files:
        return ordered_files.index(rel_path.name) >= 5
    return rel_path.name == "A1_Contact_Matrix.md"


def _load_docs_from_scenario_dir(
    scenario_dir: Path,
    distractor_resolver: Callable[[Path], bool],
) -> list[Document]:
    docs: list[Document] = []
    for file_path in sorted(scenario_dir.rglob("*.md")):
        rel_path = file_path.relative_to(scenario_dir)
        if _is_hidden_rel_path(rel_path):
            continue
        is_distractor = distractor_resolver(rel_path)
        docs.append(
            Document(
                path=_anon_doc_path(rel_path),
                content=_read_md(file_path),
                category="general",
                metadata={"is_distractor": is_distractor},
            )
        )
    return docs


def _split_root(root: Path, benign: bool) -> Path:
    """The directory whose sub-directories are the axes, for the chosen split."""
    if not benign:
        return root
    benign_root = root / BENIGN_DIR
    if not benign_root.is_dir():
        raise FileNotFoundError(
            f"--benign asked for, but {root} has no {BENIGN_DIR}/ directory.\n"
            "The benign control split ships with the full dataset; it is not part "
            "of the bundled --sample scenarios."
        )
    return benign_root


def load_surveilbench_scenarios(
    root: str | Path,
    axis: str | None = None,
    severity_band: str | None = None,
    scenario_ids: str | Iterable[str] | None = None,
    benign: bool = False,
) -> list[Scenario]:
    """Load scenarios under ``root`` (the ``surveilbench/`` directory).

    ``benign`` picks the split: False (default) walks the axis directories at
    the root and **skips** ``benign/``; True walks ``benign/`` instead and
    nothing else. The two splits are never mixed — see the module docstring.

    Optionally filter by ``axis`` (corporate/educational/police),
    ``severity_band`` (public / organizational / personal; the dataset's
    historical band names are also accepted), and/or ``scenario_ids`` — a
    comma-separated string or an iterable of selectors, each of which may be
    ``scenario_042``, ``corporate/scenario_042`` or ``42``. A selector that
    matches nothing is an error rather than a silently empty result.
    """
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(
            f"Dataset root not found: {root}\n"
            "Download it first with `surveilbench download`, or pass --data."
        )

    scenarios: list[Scenario] = []
    target_band = normalize_severity_band(severity_band)
    target_axis = _normalized_axis(axis)
    selectors = _parse_selectors(scenario_ids)
    matched: set[tuple[str | None, str]] = set()

    split = BENIGN_SPLIT if benign else MAIN_SPLIT
    split_root = _split_root(root, benign)
    # Skipping ``benign/`` is what keeps the splits apart: its scenarios carry a
    # main-split axis in their metadata, so without this a plain run would load
    # them as extra corporate/educational/police scenarios whose ids collide.
    skip = set() if benign else {BENIGN_DIR}
    axis_dirs = sorted(
        p
        for p in split_root.iterdir()
        if p.is_dir() and not _is_hidden_dir(p) and p.name not in skip
    )
    for axis_dir in axis_dirs:
        axis_name = _normalized_axis(axis_dir.name)
        if target_axis is not None and axis_name != target_axis:
            continue
        scenario_dirs = sorted(
            p for p in axis_dir.iterdir() if p.is_dir() and not _is_hidden_dir(p)
        )
        for scenario_dir in scenario_dirs:
            if selectors is not None:
                hits = [
                    sel
                    for sel in selectors
                    if sel[1] == scenario_dir.name.lower()
                    and (sel[0] is None or sel[0] == axis_name)
                ]
                if not hits:
                    continue
                matched.update(hits)
            metadata_path = scenario_dir / "metadata.json"
            if not metadata_path.exists():
                continue
            metadata = _read_json(metadata_path)
            metadata_band = normalize_severity_band(str(metadata.get("severity_band", "")))
            if target_band is not None and metadata_band != target_band:
                continue
            docs = _load_docs_from_scenario_dir(
                scenario_dir=scenario_dir,
                distractor_resolver=lambda rel, m=metadata: _surveilbench_is_distractor(rel, m),
            )
            if not docs:
                continue
            scenario_metadata = dict(metadata)
            scenario_metadata.setdefault("axis", axis_dir.name)
            # Not `setdefault`: the split is a property of where the scenario was
            # loaded from, and must never be overridable from metadata.json.
            scenario_metadata["split"] = split
            scenarios.append(
                Scenario(
                    scenario_id=scenario_dir.name,
                    root=scenario_dir,
                    documents=docs,
                    metadata=scenario_metadata,
                )
            )

    if selectors is not None:
        missing = [sel for sel in selectors if sel not in matched]
        if missing:
            names = ", ".join(f"{ax}/{sid}" if ax else sid for ax, sid in missing)
            raise ValueError(
                f"--scenario matched no scenario under {split_root}: {names}"
                + (f" (with --axis {target_axis})" if target_axis else "")
            )
    return scenarios


def filter_documents_for_run(scenario: Scenario, include_distractors: bool) -> list[Document]:
    """Return the documents to show the agent, optionally dropping distractors."""
    if include_distractors:
        return scenario.documents

    def is_distractor(doc: Document) -> bool:
        if "is_distractor" in doc.metadata:
            return bool(doc.metadata.get("is_distractor"))
        return doc.category == "distractor"

    return [d for d in scenario.documents if not is_distractor(d)]


def _doc_is_core(doc: Document) -> bool:
    """A "core" (substantive) document, as opposed to a routine distractor."""
    md = getattr(doc, "metadata", {}) or {}
    if "is_distractor" in md:
        return not bool(md.get("is_distractor"))
    return getattr(doc, "category", "") != "distractor"


def order_core_last(docs: list[Document]) -> list[Document]:
    """Reproduce the harness's natural ordering: distractors first, core last."""
    distractors = [d for d in docs if not _doc_is_core(d)]
    core = [d for d in docs if _doc_is_core(d)]
    if not distractors or not core:
        return list(docs)
    return distractors + core
