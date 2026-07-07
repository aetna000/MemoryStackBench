# MemoryStackBench

MemoryStackBench is a benchmark harness for agent memory frameworks. It runs the same multi-session scenarios against each memory stack, records transcript and memory evidence, scores behavior and auditability, and emits a static HTML scorecard that can be published with GitHub Pages.

The v0 repository is intentionally small:

- a scenario DSL with `plant -> interfere -> probe` style sessions
- a common `MemoryStackAdapter` interface
- a runner that records transcripts, memory snapshots, checks, retrieval logs, and scorecards
- a deterministic toy adapter so the harness can be tested without external services
- manifests for the full initial 16-target memory stack registry
- a static report generator for GitHub Pages

## Quickstart: Real Local Runs

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mem0,autogen-mem0,langgraph,openai-agents,graphiti,letta]"
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

## Current Local Results

The current checked-in static site contains real local runs for the `seven_sins_v0_1` suite:

| Run | Target | Score |
|---|---|---:|
| `langgraph-local` | LangGraph Store harness | `20 / 20` (`100%`) |
| `mem0-local` | Mem0 OSS | `19 / 20` (`95%`) |
| `autogen-mem0-local` | AutoGen + Mem0Memory | `19 / 20` (`95%`) |
| `letta-local` | Letta self-hosted | `19 / 20` (`95%`) |
| `openai-agents-sessions-local` | OpenAI Agents SDK Sessions | `17 / 20` (`85%`) |
| `graphiti-neo4j-local` | Graphiti + Neo4j | `14 / 20` (`70%`) |

Important interpretation notes:

- The LangGraph result is a store-level harness using `InMemoryStore` plus the benchmark adapter's explicit write/update/delete policy; it is not a built-in semantic memory agent.
- OpenAI Agents SDK Sessions is conversation-history persistence, not semantic long-term memory. Its remaining failures are raw transcript retention of poisoned webpage text and stale SFO text.
- Graphiti is a real Graphiti + Neo4j run. The current adapter scores derived `RELATES_TO` facts; several simple preference statements were not extracted into scored facts in this suite.

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

The leaderboard reads all `runs/*/scorecard.json` files and publishes quantitative overall/category scores with simple plots.
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

## Result Bundle

Each run writes:

- `run_manifest.json`
- `target_manifest.yaml`
- `transcript.jsonl`
- `memory_snapshots.jsonl`
- `retrieval_events.jsonl`
- `checks.jsonl`
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
4. Add LangMem, LlamaIndex Memory, Agno, and AutoGen + Mem0Memory as the next local Python targets.
5. Add graph/server targets: Graphiti, Cognee, Letta, CrewAI, and Zep.
6. Add hosted/cloud targets only with strict cleanup: Supermemory, Hindsight, Google ADK Memory Bank, AWS Bedrock AgentCore Memory, and OpenAI Agents SDK Sessions.
7. Add VM runner isolation after the first framework adapters are producing stable bundles.
8. Add evidence hashing and Merkle roots once output schemas stop changing.

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
