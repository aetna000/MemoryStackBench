from __future__ import annotations

import importlib
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter


def load_adapter(target_manifest: dict[str, Any]) -> MemoryStackAdapter:
    adapter_spec = target_manifest.get("adapter") or {}
    module_name = adapter_spec.get("module")
    class_name = adapter_spec.get("class")
    if not module_name or not class_name:
        raise ValueError("Target manifest must include adapter.module and adapter.class")

    module = importlib.import_module(str(module_name))
    adapter_cls = getattr(module, str(class_name))
    if not issubclass(adapter_cls, MemoryStackAdapter):
        raise TypeError(f"{module_name}.{class_name} must inherit MemoryStackAdapter")
    return adapter_cls(target_manifest)

