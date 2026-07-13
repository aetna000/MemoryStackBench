from pathlib import Path

import yaml


EXPECTED_TARGETS = {
    "mem0.yaml",
    "zep.yaml",
    "graphiti.yaml",
    "letta.yaml",
    "langgraph.yaml",
    "langmem.yaml",
    "cognee.yaml",
    "llamaindex_memory.yaml",
    "crewai_memory.yaml",
    "agno_memory.yaml",
    "autogen_mem0memory.yaml",
    "google_adk_memory_bank.yaml",
    "aws_bedrock_agentcore_memory.yaml",
    "openai_agents_sdk_sessions.yaml",
    "supermemory.yaml",
    "hindsight.yaml",
    "tencentdb_agent_memory.yaml",
    "aetnamem.yaml",
    "tree_ring_memory.yaml",
}


def test_full_initial_target_registry_exists() -> None:
    target_files = {path.name for path in Path("targets").glob("*.yaml")}

    assert EXPECTED_TARGETS <= target_files


def test_target_manifests_have_required_fields() -> None:
    for path in Path("targets").glob("*.yaml"):
        with path.open("r", encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle)

        assert manifest["id"]
        assert manifest["framework"]
        assert manifest["mode"]
        assert manifest["adapter"]["module"]
        assert manifest["adapter"]["class"]
