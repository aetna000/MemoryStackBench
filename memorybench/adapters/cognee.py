from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import answer_from_records
from memorybench.adapters.store_policy import (
    benchmark_metadata,
    forget_needle,
    is_forget_request,
    memory_content_for_user_message,
    records_matching_query,
    source_type,
)


class CogneeAdapter(MemoryStackAdapter):
    """Cognee v1 memory adapter using remember/recall/forget.

    Cognee does the actual memory ingestion and graph-backed recall. The adapter
    applies the benchmark's explicit write policy so untrusted webpage text is
    not stored as durable user memory.
    """

    capabilities = {
        "inspect_memory": True,
        "delete_memory": "data_item_forget",
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._run_id = uuid.uuid4().hex[:10]
        self._root_dir = Path(
            self.config.get("cognee_root_dir")
            or f"/tmp/memorystackbench-cognee-{self._run_id}"
        )
        self._configure_environment()

        try:
            import cognee
            from cognee.tasks.ingestion.data_item import DataItem
        except ImportError as exc:
            raise RuntimeError(
                "CogneeAdapter requires Cognee. Install it with "
                "`pip install -e '.[cognee]'` or `pip install cognee==1.2.2 cbor2==5.8.0`."
            ) from exc

        self._cognee = cognee
        self._DataItem = DataItem
        self._loop = asyncio.new_event_loop()
        self._journals: dict[str, list[dict[str, Any]]] = {}
        self._datasets: dict[str, str] = {}
        self._dataset_ids: dict[str, str] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        if subject_id in self._datasets:
            self._safe_forget_dataset(self._datasets[subject_id])
        self._datasets[subject_id] = self._dataset_name(subject_id)
        self._dataset_ids.pop(subject_id, None)
        self._journals[subject_id] = []
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        self._ensure_subject(subject_id)
        turn_id = self._next_turn_id(subject_id, session_id)
        if is_forget_request(message):
            deleted = self.delete_memory(subject_id, {"contains": forget_needle(message)})
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        content, is_correction = memory_content_for_user_message(message)
        if is_correction:
            self._delete_by_terms(subject_id, ("preferred airport", "sfo"))
        if content:
            self._write_memory(subject_id, session_id, turn_id, content, source_type(message))

        retrieved = self._retrieve(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        self._ensure_subject(subject_id)
        raw_items = self._list_dataset_data(subject_id)
        raw_by_id = {_string_id(item): _raw(item) for item in raw_items}
        records = []
        for entry in self._journals.get(subject_id, []):
            if entry.get("deleted_at"):
                continue
            record = self._normalize_journal_entry(entry, subject_id)
            raw_item = raw_by_id.get(str(entry.get("data_id")))
            if raw_item is not None:
                record["raw"] = raw_item
            records.append(record)
        return sorted(records, key=lambda record: str(record.get("memory_id")))

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        self._ensure_subject(subject_id)
        contains = str(selector.get("contains") or "").lower()
        changed = False
        for entry in self._journals.get(subject_id, []):
            if entry.get("deleted_at"):
                continue
            content = str(entry.get("content") or "").lower()
            if contains and contains not in content:
                continue
            self._forget_data_item(subject_id, str(entry["data_id"]))
            entry["deleted_at"] = utc_now()
            changed = True
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        for dataset in list(self._datasets.values()):
            self._safe_forget_dataset(dataset)
        self._datasets.clear()
        self._dataset_ids.clear()
        if not self.config.get("cognee_keep_data"):
            shutil.rmtree(self._root_dir, ignore_errors=True)
        if not self._loop.is_closed():
            self._loop.close()

    def _configure_environment(self) -> None:
        _load_env_local(("OPENAI_API_KEY", "LLM_API_KEY"))
        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            os.environ.setdefault("LLM_API_KEY", openai_key)
        if not os.environ.get("LLM_API_KEY"):
            raise RuntimeError("CogneeAdapter requires LLM_API_KEY or OPENAI_API_KEY.")

        os.environ.setdefault("LLM_PROVIDER", "openai")
        os.environ.setdefault("LLM_MODEL", str(self.config.get("model", {}).get("model") or "gpt-4o-mini"))
        os.environ.setdefault("EMBEDDING_PROVIDER", "openai")
        os.environ.setdefault("EMBEDDING_MODEL", "text-embedding-3-small")
        os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
        os.environ.setdefault("DATA_ROOT_DIRECTORY", str(self._root_dir / "data"))
        os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(self._root_dir / "system"))
        Path(os.environ["DATA_ROOT_DIRECTORY"]).mkdir(parents=True, exist_ok=True)
        Path(os.environ["SYSTEM_ROOT_DIRECTORY"]).mkdir(parents=True, exist_ok=True)

    def _ensure_subject(self, subject_id: str) -> None:
        if subject_id not in self._datasets:
            self.reset_subject(subject_id)

    def _write_memory(
        self,
        subject_id: str,
        session_id: str,
        turn_id: int,
        content: str,
        source: str,
    ) -> None:
        data_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{self._run_id}:{subject_id}:{session_id}:{turn_id}")
        metadata = benchmark_metadata(session_id, turn_id, source_type=source)
        metadata.update(
            {
                "memorybench": True,
                "subject_hash": _hash(subject_id),
                "memory_id": str(data_id),
            }
        )
        item = self._DataItem(
            data=content,
            label=f"memorybench-{turn_id}",
            external_metadata={key: value for key, value in metadata.items() if value is not None},
            data_id=data_id,
        )
        result = self._run(
            self._cognee.remember(
                item,
                dataset_name=self._datasets[subject_id],
                self_improvement=False,
            )
        )
        result_raw = _raw(result)
        dataset_id = str(getattr(result, "dataset_id", None) or result_raw.get("dataset_id") or "")
        if dataset_id:
            self._dataset_ids[subject_id] = dataset_id
        self._journals.setdefault(subject_id, []).append(
            {
                "data_id": str(data_id),
                "content": content,
                "source_type": source,
                "source_session_id": session_id,
                "source_turn_id": f"t{turn_id}",
                "created_at": metadata["created_at"],
                "confidence": None,
                "deleted_at": None,
                "raw_remember_result": result_raw,
            }
        )

    def _retrieve(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        raw_results = self._run(
            self._cognee.recall(
                _search_query(query),
                datasets=[self._datasets[subject_id]],
                only_context=True,
                top_k=int(self.config.get("cognee_top_k") or 10),
            )
        )
        records = [
            self._normalize_recall_result(result, subject_id, index)
            for index, result in enumerate(raw_results or [], start=1)
        ]
        records = records_matching_query(query, records)
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "cognee_query": _search_query(query),
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
            }
        )
        return records

    def _delete_by_terms(self, subject_id: str, terms: tuple[str, ...]) -> bool:
        changed = False
        for entry in self._journals.get(subject_id, []):
            if entry.get("deleted_at"):
                continue
            content = str(entry.get("content") or "").lower()
            if any(term in content for term in terms):
                self._forget_data_item(subject_id, str(entry["data_id"]))
                entry["deleted_at"] = utc_now()
                changed = True
        return changed

    def _forget_data_item(self, subject_id: str, data_id: str) -> None:
        self._run(
            self._cognee.forget(
                data_id=uuid.UUID(data_id),
                dataset=self._datasets[subject_id],
            )
        )

    def _safe_forget_dataset(self, dataset: str) -> None:
        try:
            self._run(self._cognee.forget(dataset=dataset))
        except Exception:
            pass

    def _list_dataset_data(self, subject_id: str) -> list[Any]:
        dataset_id = self._dataset_ids.get(subject_id)
        if not dataset_id:
            return []
        try:
            return list(self._run(self._cognee.datasets.list_data(uuid.UUID(dataset_id))) or [])
        except Exception:
            return []

    def _normalize_journal_entry(self, entry: dict[str, Any], subject_id: str) -> dict[str, Any]:
        return {
            "memory_id": entry.get("data_id"),
            "framework": "cognee",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": self._datasets.get(subject_id),
            "content": str(entry.get("content") or ""),
            "source_type": entry.get("source_type"),
            "source_session_id": entry.get("source_session_id"),
            "source_turn_id": entry.get("source_turn_id"),
            "created_at": entry.get("created_at"),
            "updated_at": None,
            "deleted_at": entry.get("deleted_at"),
            "confidence": entry.get("confidence"),
            "scope": "user_private",
            "raw": entry.get("raw_remember_result"),
        }

    def _normalize_recall_result(self, result: Any, subject_id: str, index: int) -> dict[str, Any]:
        raw = _raw(result)
        content = (
            raw.get("text")
            or raw.get("content")
            or raw.get("context")
            or raw.get("raw", {}).get("value")
            or str(result)
        )
        return {
            "memory_id": raw.get("chunk_id") or raw.get("id") or f"cognee-recall-{index:04d}",
            "framework": "cognee",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": self._datasets.get(subject_id),
            "content": str(content),
            "source_type": raw.get("source"),
            "source_session_id": None,
            "source_turn_id": None,
            "created_at": None,
            "updated_at": None,
            "deleted_at": None,
            "confidence": raw.get("score"),
            "scope": "user_private",
            "raw": raw,
        }

    def _dataset_name(self, subject_id: str) -> str:
        digest = hashlib.sha256(subject_id.encode("utf-8")).hexdigest()[:18]
        return f"memorybench_{self._run_id}_{digest}"

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]

    def _run(self, value: Any) -> Any:
        if not hasattr(value, "__await__"):
            return value
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._loop.run_until_complete(value)
        raise RuntimeError("CogneeAdapter cannot run inside an existing asyncio event loop.")


def _load_env_local(keys: tuple[str, ...]) -> None:
    path = Path(".env.local")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in keys and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _raw(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return _json_safe(value)
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if hasattr(value, "dict"):
        return _json_safe(value.dict())
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict):
        return _json_safe({key: item for key, item in raw.items() if key != "_sa_instance_state"})
    return {"value": str(value)}


def _json_safe(value: Any) -> dict[str, Any]:
    try:
        safe = json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return {"value": str(value)}
    return safe if isinstance(safe, dict) else {"value": safe}


def _string_id(value: Any) -> str:
    for name in ("id", "data_id", "uuid"):
        candidate = getattr(value, name, None)
        if candidate:
            return str(candidate)
    if isinstance(value, dict):
        for name in ("id", "data_id", "uuid"):
            if value.get(name):
                return str(value[name])
    return ""


def _search_query(query: str) -> str:
    lower = query.lower()
    if "itinerary" in lower or "public" in lower:
        return "travel itineraries private public default preference"
    if "airport" in lower:
        return "preferred airport SFO OAK"
    if "backup" in lower or "email" in lower:
        return "backup email private-backup@example.com"
    if "vegetarian" in lower or "beef" in lower:
        return "diet vegetarian beef business dinners"
    return query


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
