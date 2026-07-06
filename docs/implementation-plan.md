# Implementation Plan

Use `plan.md` as the research thesis and this document as the build plan.

## V0: Harness First

Deliverables:

- scenario DSL
- adapter contract
- deterministic toy adapter
- JSONL evidence bundle
- category scorecard
- static HTML report
- GitHub Pages publishing path

Why: the benchmark is only credible if the harness itself is reproducible before testing live frameworks.

## Target Scope

The first public project scope is the 16-target registry in `docs/target-registry.md`:

- Mem0
- Zep
- Graphiti
- Letta
- LangGraph
- LangMem
- Cognee
- LlamaIndex Memory
- CrewAI Memory
- Agno Memory
- AutoGen + Mem0Memory
- Google ADK + Memory Bank
- AWS Bedrock AgentCore Memory
- OpenAI Agents SDK Sessions
- Supermemory
- Hindsight

## V1: First Real Framework Targets

Validate Mem0 locally first, then build LangGraph.

Reasons:

- Mem0 now has a local OSS adapter path and gives us the first real memory-layer run.
- it can run locally without relying on a managed memory service
- it has clear short-term and long-term memory concepts
- it is a good reference for testing thread isolation and store namespace isolation

Target variants:

- `langgraph_checkpointer_only`
- `langgraph_store_enabled`

Required scenarios:

- thread continuity
- cross-thread user preference retrieval
- stale preference after update
- checkpoint replay after deletion
- store namespace isolation

## V2: Mem0

Stabilize the wrapper agent with a fixed model and pinned Mem0 package version.

Required adapter operations:

- reset subject with delete-all or isolated user ids
- add conversation events
- search relevant memories
- delete specific memory records where supported
- normalize native memory records

Primary tests:

- untrusted content stored as durable preference
- wrong fact updated instead of replaced
- forget request not honored
- source metadata missing
- overgeneralized extraction

## V3: Zep

Before Zep, implement the other local Python targets where backend setup is cheap:

- LangMem
- LlamaIndex Memory
- Agno Memory
- AutoGen + Mem0Memory

Then implement graph/server-backed local targets:

- Graphiti
- Cognee
- Letta
- CrewAI Memory

## V4: Zep

Split targets into cloud and self-hosted if both are supported.

Required adapter operations:

- create isolated users and threads
- add session messages
- retrieve context block
- inspect facts/graph/context where available
- delete test users after runs

Primary tests:

- old fact versus new fact
- valid-time behavior
- source provenance
- third-party claim becoming first-party fact
- cross-user graph contamination

## V5: Hosted and Managed Targets

Use strict lifecycle cleanup and separate accounts/projects where possible:

- Supermemory
- Hindsight
- Google ADK + Memory Bank
- AWS Bedrock AgentCore Memory
- OpenAI Agents SDK Sessions

OpenAI Agents SDK Sessions should be reported separately as session/conversation memory unless a semantic long-term memory layer is added.

## Letta Notes

Use self-hosted Letta first so the run is reproducible.

Required adapter operations:

- create benchmark agent
- run sessions against the same agent identity
- inspect memory blocks, messages, and archival memory
- delete the benchmark agent/database records between scenarios

Primary tests:

- agent edits core memory after poisoned prompt
- shared memory block leakage
- old messages retrieved after deletion request
- agent-generated memory without user evidence

## V6: Runner Isolation

Only add VM orchestration after the first three real adapters are stable.

The VM runner should:

- provision a clean Ubuntu instance
- install Docker and Python
- fetch the pinned benchmark repo
- start the target runtime
- run health checks
- run the suite
- upload the result bundle
- destroy the VM

Do not run arbitrary discovered repos. Candidate targets should become pinned registry PRs reviewed by a human.

## V7: Public Results Site

Publish:

- latest scorecards by framework
- evidence bundle links
- target manifest and pinned version
- scenario suite version
- methodology page
- changelog for suite changes

Avoid a single leaderboard score. Show category failures prominently.
