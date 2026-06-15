"""Load SurveilBench scenarios from a dataset root directory.

A dataset root looks like::

    surveilbench/
      corporate/scenario_001/ ... scenario_101/
      educational/scenario_001/ ...
      police/scenario_001/ ...

Each scenario folder holds 11 Markdown documents (5 core + 5 routine
distractors + ``A1_Contact_Matrix.md``) and a ``metadata.json``.

To match the evaluation harness, document paths shown to the agent are
anonymized (``doc_<hash>.md``) and the five "core" documents are ordered to
appear after the distractors (see :func:`surveilbench.env` / the agent runner).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .types import Document, Scenario, normalize_severity_band

AXES = ("corporate", "educational", "police")


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


def load_surveilbench_scenarios(
    root: str | Path,
    axis: str | None = None,
    severity_band: str | None = None,
) -> list[Scenario]:
    """Load scenarios under ``root`` (the ``surveilbench/`` directory).

    Optionally filter by ``axis`` (corporate/educational/police) and/or
    ``severity_band`` (public / organizational / personal; the dataset's
    historical band names are also accepted).
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

    axis_dirs = sorted(p for p in root.iterdir() if p.is_dir() and not _is_hidden_dir(p))
    for axis_dir in axis_dirs:
        axis_name = _normalized_axis(axis_dir.name)
        if target_axis is not None and axis_name != target_axis:
            continue
        scenario_dirs = sorted(
            p for p in axis_dir.iterdir() if p.is_dir() and not _is_hidden_dir(p)
        )
        for scenario_dir in scenario_dirs:
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
            scenarios.append(
                Scenario(
                    scenario_id=scenario_dir.name,
                    root=scenario_dir,
                    documents=docs,
                    metadata=scenario_metadata,
                )
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
