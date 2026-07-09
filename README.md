# MemoryStackBench

MemoryStackBench is a benchmark harness for agent memory frameworks. It runs the same multi-session scenarios against each memory stack, records transcript and memory evidence, scores safety behavior, reports a separate auditability matrix, captures informational adapter timings, and emits static HTML scorecards that can be published with GitHub Pages.

The v0 repository is intentionally small:

- a scenario DSL with `plant -> interfere -> probe` style sessions
- a common `MemoryStackAdapter` interface
- a runner that records transcripts, memory snapshots, checks, retrieval logs, and scorecards
- a deterministic toy adapter so the harness can be tested without external services
- manifests for the full initial 18-target memory stack registry
- a static report generator for GitHub Pages

## Quickstart: Real Local Runs

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,aetnamem,mem0,zep,autogen-mem0,langgraph,llamaindex,langmem,agno,cognee,hindsight,openai-agents,graphiti,letta,aws,google-adk]"
export OPENAI_API_KEY="..."

memorybench run \
  --target targets/mem0.yaml \
  --suite suites/seven_sins_v0_1 \
  --out runs/mem0-local

memorybench report --run runs/mem0-local --site site/mem0-local
memorybench leaderboard --runs runs --out site/leaderboard
```

Open `site/index.html` or `site/leaderboard/index.html` in a browser.

Run tests:

```bash
pytest
```

CrewAI 1.15.1 is pinned as an optional dependency, but on this macOS/Rosetta Python host its LanceDB pin does not resolve. Use Colima or another Linux Docker runtime for that target:

```bash
docker run --rm --env-file .env.local -v "$PWD:/work" -w /work python:3.12-slim \
  sh -lc 'python -m pip install -q --upgrade pip && python -m pip install -q -e ".[dev,crewai]" && python -m memorybench.cli run --target targets/crewai_memory.yaml --suite suites/seven_sins_v0_1 --out runs/crewai-memory-local'
```

## Current Results

After the hardening review, `seven_sins_v0_1` contains 5 scenario-level tests and 33 check-level assertions. The checked-in static site now includes hardened reruns for all currently publishable local, hosted, Docker, and API-backed targets.

| Run | Target | Checks | Scenarios | Failures |
|---|---|---:|---:|---:|
| `aetnamem-local` | aetnamem embedded SQLite auditable memory engine | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `agno-memory-local` | Agno MemoryManager harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `aws-agentcore-memory-local` | AWS Bedrock AgentCore Memory event-memory harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `cognee-local` | Cognee remember/recall/forget harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `crewai-memory-local` | CrewAI unified Memory harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `google-adk-memory-bank-local` | Google ADK / Agent Platform Memory Bank harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `hindsight-local` | Hindsight retain/recall/list/delete harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `langgraph-local` | LangGraph Store harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `langmem-local` | LangMem manage/search tools harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `llamaindex-memory-local` | LlamaIndex ChatMemoryBuffer harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `supermemory-api-local` | Supermemory hosted direct memory API | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `zep-cloud-local` | Zep Cloud user graph harness | `33 / 33` (`100%`) | `5 / 5` (`100%`) | `0` |
| `letta-local` | Letta self-hosted memory blocks | `31 / 33` (`94%`) | `3 / 5` (`60%`) | `2` |
| `autogen-mem0-local` | AutoGen + Mem0Memory | `30 / 33` (`91%`) | `3 / 5` (`60%`) | `3` |
| `mem0-local` | Mem0 OSS APIs directly | `30 / 33` (`91%`) | `3 / 5` (`60%`) | `3` |
| `openai-agents-sessions-local` | OpenAI Agents SDK Sessions | `30 / 33` (`91%`) | `3 / 5` (`60%`) | `3` |
| `tencentdb-agent-memory-local` | TencentDB Agent Memory standalone gateway | `26 / 33` (`79%`) | `2 / 5` (`40%`) | `7` |
| `graphiti-neo4j-local` | Graphiti + Neo4j | `24 / 33` (`73%`) | `1 / 5` (`20%`) | `9` |

The deterministic toy adapter is a harness smoke test, not a leaderboard target. Under the hardened suite it passes `2 / 5` scenarios and `26 / 33` checks, which is useful for validating that the benchmark catches naive memory behavior.

Important interpretation notes:

- The AWS result is a real AWS Bedrock AgentCore Memory managed-service run using short-term event memory APIs: `create_memory`, `create_event`, `list_events`, and `delete_event`. It is an `implemented_store_harness` result with explicit benchmark write/retrieval/delete policy, not a score for AgentCore's asynchronous long-term semantic extraction strategies.
- The Zep result is a real Zep Cloud run using temporary users/threads, user graph writes, and graph search. It is an `implemented_store_harness` result with explicit benchmark write/retrieval/delete policy, not a broad measurement of every automatic extraction path in Zep Cloud.
- The LangGraph result is a store-level harness using `InMemoryStore` plus the benchmark adapter's explicit write/update/delete policy; it is not a built-in semantic memory agent.
- The LlamaIndex, LangMem, and Agno results are local Python memory/store harnesses using their real memory APIs plus the same explicit benchmark write/update/delete policy for comparability.
- The Cognee result is a real local Cognee 1.2.2 run using temporary datasets, `DataItem` + `remember()`, graph-backed `recall(..., only_context=True)`, and `forget()` cleanup. It is an `implemented_store_harness` result, not a score for every possible automatic agent integration built on Cognee.
- The CrewAI result is a real CrewAI 1.15.1 run in Linux/Colima using unified `Memory`, LanceDB path storage, `remember()`, shallow `recall()`, `list_records()`, and `forget(record_ids=...)`. It is an `implemented_store_harness` result with explicit benchmark write/delete policy.
- The Google ADK + Memory Bank result is a real Agent Platform Memory Bank run using a temporary Agent Engine, `memories.create()`, `memories.retrieve()`, `memories.list()`, and `memories.delete()`. The temporary engine was force-deleted after the run and verified as gone.
- The Hindsight result is a real self-hosted Hindsight 0.8.4 slim Docker run on Colima using temporary banks, `retain()`, `recall()`, native `list_memories()`, and document deletion. It uses OpenAI `gpt-4o-mini`, OpenAI `text-embedding-3-small` embeddings, and RRF reranking.
- The aetnamem result is a real local run of the embedded SQLite engine using deterministic extraction, quarantine of untrusted content, fact-slot supersession, deletion receipts, and retrieval audit events.
- The Supermemory result is a real hosted API run using direct memory-entry create/search/list/forget endpoints. It does not measure Supermemory document ingestion, user profiles, connectors, or self-hosted mode.
- The Mem0 and AutoGen + Mem0Memory reruns still show high-severity failures on webpage poisoning and deleted-email retention.
- The Letta rerun mainly fails provenance checks: the right preference appears, but the scored record metadata points at later sessions rather than the original user statement.
- The OpenAI Agents SDK Sessions rerun measures conversation-history persistence, not semantic long-term memory. Its remaining failures are raw transcript retention of poisoned webpage text and stale SFO text.
- The Graphiti rerun is a real Graphiti + Neo4j run. The current adapter scores derived graph facts; several simple preference statements were not extracted into scored facts.
- The TencentDB Agent Memory result is a real local standalone gateway run using the upstream Node.js package, auto-capture, L1 extraction, keyword recall, native conversation search, and SQLite evidence inspection. The public gateway does not expose a direct record-delete route, so deletion is tested through the user's natural-language forget request. Remaining failures include webpage poisoning, stored webpage poison content, stale/raw SFO retention, and backup email retention after a forget request.

A plain-language explanation for newer agent builders is published at:

https://aetna000.github.io/MemoryStackBench/guide/

## Local Mem0 Commands

The Mem0 target is wired for the open-source Python SDK:

```bash
pip install -e ".[dev,mem0]"
export OPENAI_API_KEY="..."

memorybench run \
  --target targets/mem0.yaml \
  --suite suites/seven_sins_v0_1 \
  --out runs/mem0-local

memorybench report --run runs/mem0-local --site site/mem0-local
memorybench leaderboard --runs runs --out site/leaderboard
```

See [docs/mem0-local.md](docs/mem0-local.md) for custom Mem0 config and report publishing.

The leaderboard reads all `runs/*/scorecard.json` files and publishes quantitative overall/category safety scores with simple plots. It also publishes `auditability.json`, a separate evidence matrix for inspectability, provenance, retrieval transparency, deletion evidence, mutation lineage, and tamper evidence.
Internal smoke-test targets such as `toy` are filtered out of the leaderboard.

## Development Smoke Test

The repository includes a deterministic `toy` adapter only to validate the harness without external services:

```bash
memorybench run \
  --target targets/toy.yaml \
  --suite suites/seven_sins_v0_1 \
  --out runs/local-toy
```

The toy target must not be reported as a framework result. It is excluded from the public leaderboard.

## Target Registry

The initial benchmark scope covers:

1. Mem0
2. Zep
3. Graphiti
4. Letta
5. LangGraph
6. LangMem
7. Cognee
8. LlamaIndex Memory
9. CrewAI Memory
10. Agno Memory
11. AutoGen + Mem0Memory
12. Google ADK + Memory Bank
13. AWS Bedrock AgentCore Memory
14. OpenAI Agents SDK Sessions
15. Supermemory
16. Hindsight
17. TencentDB Agent Memory
18. aetnamem

See [docs/target-registry.md](docs/target-registry.md) for manifests, implementation status, and source links.

## Benchmark Contract

Every target is wrapped by an adapter with the same shape:

```python
class MemoryStackAdapter:
    def reset_subject(self, subject_id: str) -> None: ...
    def start_session(self, subject_id: str, session_id: str) -> None: ...
    def send(self, subject_id: str, session_id: str, message: str) -> str: ...
    def inspect_memory(self, subject_id: str) -> list[dict] | None: ...
    def delete_memory(self, subject_id: str, selector: dict) -> bool | None: ...
    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict] | None: ...
```

The runner does not need native framework details. Adapters normalize memory into JSON records with content, source, timestamps, confidence, scope, and raw native data when available.

## Score Axes

MemoryStackBench keeps methodology axes separate:

- Safety: scenario pass/fail checks from `scorecard.json`.
- Auditability: an evidence matrix in `auditability_scorecard.json`, with each dimension marked by origin such as `native`, `adapter_injected`, or `undeclared`.
- Timing: per-adapter-operation timings in `timings.jsonl`. These are informational for safety runs and should not be treated as a performance leaderboard.

A dedicated performance suite should use synthetic corpora, repeated runs, corpus-size sweeps, runner hardware metadata, and LLM/API call accounting before making speed ranking claims.

## Result Bundle

Each run writes:

- `run_manifest.json`
- `target_manifest.yaml`
- `transcript.jsonl`
- `memory_snapshots.jsonl`
- `retrieval_events.jsonl`
- `timings.jsonl`
- `checks.jsonl`
- `auditability_scorecard.json`
- `scorecard.json`
- `scorecard.html`
- `failure_report.md`

That bundle is the public evidence for a score.

## GitHub Pages

For public results, keep generated pages under `site/` and publish them with the included GitHub Pages workflow:

```bash
memorybench run --target targets/mem0.yaml --suite suites/seven_sins_v0_1 --out runs/mem0-local
memorybench report --run runs/mem0-local --site site/mem0-local
memorybench leaderboard --runs runs --out site/leaderboard
```

Commit `site/` for a simple static site, or have CI upload `site/` as a Pages artifact.

## Implementation Roadmap

1. Keep the harness deterministic with the toy adapter and tests.
2. Run Mem0 locally with the current adapter and pin the exact package version after the first clean run.
3. Add LangGraph because it can be fully local and makes short-term versus long-term memory scope explicit.
4. Add Cognee and CrewAI with pinned runtime isolation and native inspect/delete behavior.
5. Add hosted/cloud targets only with strict cleanup: Supermemory, Hindsight, and Google ADK Memory Bank.
6. Split existing store/session harnesses from automatic-extraction variants where the framework supports both.
7. Keep auditability as a separate matrix and classify native versus adapter-provided evidence in target manifests.
8. Add a dedicated performance suite with corpus-size sweeps and LLM/API call accounting.
9. Add VM runner isolation after the first framework adapters are producing stable bundles.
10. Add evidence hashing and Merkle roots once output schemas stop changing.

## Public Framing

The first public report should avoid a vague overall score. Use category scores:

- memory write correctness
- retrieval correctness
- deletion behavior
- tenant/user isolation
- untrusted-source resistance
- temporal update handling
- provenance quality
- auditability

The useful claim is: fixed model, fixed scenarios, changing memory layer.
