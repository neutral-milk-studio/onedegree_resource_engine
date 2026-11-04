"""Source registry, driven by ``config/sources.yaml``.

Adding a source = one mapper module under ``ingest/adapters/`` + one entry in the YAML.
The pipeline never changes.
"""

from __future__ import annotations

import importlib
import os
from functools import cache
from pathlib import Path

import yaml

from ingest.adapters.base import REPO_ROOT, ModuleAdapter

DEFAULT_CONFIG = REPO_ROOT / "config" / "sources.yaml"


class UnknownSource(KeyError):
    """Raised for a source name that has no entry in the source config."""


@cache
def _config(path: str) -> dict[str, dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data.get("sources", {})


def config_path() -> str:
    return os.environ.get("RE_SOURCES_CONFIG", str(DEFAULT_CONFIG))


def source_names() -> list[str]:
    return sorted(_config(config_path()))


def get_adapter(name: str) -> ModuleAdapter:
    entries = _config(config_path())
    if name not in entries:
        raise UnknownSource(f"unknown source {name!r}; configured sources: {sorted(entries)}")
    entry = entries[name]
    module = importlib.import_module(entry["module"])
    return ModuleAdapter(module, inputs=list(entry.get("inputs", [])), name=name)
