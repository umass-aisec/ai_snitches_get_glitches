"""Download and locate the SurveilBench dataset.

The dataset lives in the **gated** Hugging Face dataset ``juniworld/surveilbench``
and ships as ``surveilbench.zip``. Because it is gated you must request access on
the dataset page and authenticate (``huggingface-cli login`` or set ``HF_TOKEN``)
before downloading.

A resolved dataset *root* is the directory that directly contains the three axis
folders (``corporate/``, ``educational/``, ``police/``).
"""

from __future__ import annotations

import os
import zipfile
from importlib import resources
from pathlib import Path

HF_REPO_ID = "juniworld/surveilbench"
HF_ZIP_NAME = "surveilbench.zip"

_AXES = ("corporate", "educational", "police")


def sample_data_root() -> Path:
    """Path to the 3 fictional scenarios bundled for offline smoke tests."""
    return Path(resources.files("surveilbench").joinpath("sample_data"))


def _looks_like_root(path: Path) -> bool:
    return path.is_dir() and any((path / ax).is_dir() for ax in _AXES)


def _resolve_root(candidate: Path) -> Path | None:
    """Accept either the root itself or a parent containing ``surveilbench/``."""
    candidate = candidate.expanduser()
    if _looks_like_root(candidate):
        return candidate
    nested = candidate / "surveilbench"
    if _looks_like_root(nested):
        return nested
    return None


def find_dataset(explicit: str | os.PathLike | None = None) -> Path:
    """Locate an already-downloaded dataset root.

    Search order: ``explicit`` arg → ``$SURVEILBENCH_DATA`` → ``./data`` →
    ``~/.cache/surveilbench``. Raises a helpful error if none is found.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("SURVEILBENCH_DATA")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "data")
    candidates.append(Path.home() / ".cache" / "surveilbench")

    for cand in candidates:
        root = _resolve_root(cand)
        if root is not None:
            return root

    searched = "\n  ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "Could not find the SurveilBench dataset. Looked in:\n  "
        f"{searched}\n\n"
        "Download it with `surveilbench download`, or pass an explicit path via "
        "--data / the SURVEILBENCH_DATA environment variable."
    )


def download_dataset(
    dest: str | os.PathLike | None = None,
    *,
    force: bool = False,
    token: str | None = None,
) -> Path:
    """Download ``surveilbench.zip`` from Hugging Face and extract it.

    Returns the dataset root (``<dest>/surveilbench``). Requires access to the
    gated dataset; pass ``token`` or set ``HF_TOKEN`` / log in beforehand.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "huggingface_hub is required to download the dataset: pip install huggingface_hub"
        ) from e

    dest = Path(dest).expanduser() if dest is not None else (Path.cwd() / "data")
    dest.mkdir(parents=True, exist_ok=True)

    existing = _resolve_root(dest)
    if existing is not None and not force:
        return existing

    zip_path = hf_hub_download(
        repo_id=HF_REPO_ID,
        repo_type="dataset",
        filename=HF_ZIP_NAME,
        token=token,
    )
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)

    root = _resolve_root(dest)
    if root is None:
        raise RuntimeError(
            f"Extracted {HF_ZIP_NAME} into {dest} but found no axis folders "
            f"({', '.join(_AXES)}). The archive layout may have changed."
        )
    return root
