# Target Registry

MemoryStackBench will track these memory systems as first-class benchmark targets.

Status meanings:

- `implemented`: adapter can run against the scenario suite.
- `implemented_store_harness`: adapter runs against a real storage primitive with an explicit benchmark write/retrieval policy.
- `reference_only`: useful for harness validation, not a publishable framework score.
- `pending_adapter`: manifest exists, but a concrete adapter still needs to be implemented.

| # | Target | Manifest | Status | First adapter focus |
|---|---|---|---|---|
| 1 | Mem0 | `targets/mem0.yaml` | implemented | OSS `Memory.add/search/get_all/delete_all` |
| 2 | Zep | `targets/zep.yaml` | implemented_store_harness | temporary users, threads, user graph writes, graph search |
| 3 | Graphiti | `targets/graphiti.yaml` | implemented | temporal graph episodes, search, provenance |
| 4 | Letta | `targets/letta.yaml` | implemented | agents, memory blocks, messages, archival memory |
| 5 | LangGraph | `targets/langgraph.yaml` | implemented_store_harness | store-enabled persistence variant |
| 6 | LangMem | `targets/langmem.yaml` | pending_adapter | extracted semantic memory over LangGraph store |
| 7 | Cognee | `targets/cognee.yaml` | pending_adapter | graph/vector memory ingestion and search |
| 8 | LlamaIndex Memory | `targets/llamaindex_memory.yaml` | pending_adapter | short-term queue and long-term memory blocks |
| 9 | CrewAI Memory | `targets/crewai_memory.yaml` | pending_adapter | built-in crew memory and persistence path |
| 10 | Agno Memory | `targets/agno_memory.yaml` | pending_adapter | automatic user memory with persistent DB |
| 11 | AutoGen + Mem0Memory | `targets/autogen_mem0memory.yaml` | implemented | `autogen_ext.memory.mem0.Mem0Memory` |
| 12 | Google ADK + Memory Bank | `targets/google_adk_memory_bank.yaml` | pending_adapter | ADK memory service and Vertex/Agent Platform Memory Bank |
| 13 | AWS Bedrock AgentCore Memory | `targets/aws_bedrock_agentcore_memory.yaml` | implemented_store_harness | managed short-term event memory APIs |
| 14 | OpenAI Agents SDK Sessions | `targets/openai_agents_sdk_sessions.yaml` | implemented | session persistence across agent runs |
| 15 | Supermemory | `targets/supermemory.yaml` | pending_adapter | hosted memory API ingestion, search, delete |
| 16 | Hindsight | `targets/hindsight.yaml` | pending_adapter | retain, recall, reflect memory operations |

Additional harness targets:

- `targets/toy.yaml`: deterministic naive target for harness tests.
- `targets/langgraph_reference.yaml`: local reference target only, not a publishable LangGraph score.

## Build Order

The lowest-risk implementation order is:

1. Mem0 local OSS: implemented and scored.
2. AutoGen + Mem0Memory: implemented and scored.
3. LangGraph store harness: implemented and scored.
4. OpenAI Agents SDK Sessions: implemented and scored as session-history memory.
5. Graphiti + Neo4j: implemented and scored.
6. Letta self-hosted: implemented and scored.
7. AWS Bedrock AgentCore Memory event-memory harness: implemented and scored with temporary resource cleanup.
8. Zep Cloud user graph harness: implemented and scored with temporary user cleanup.
9. LangMem: builds naturally on the LangGraph store path.
10. LlamaIndex Memory: local Python target with inspectable memory classes.
11. Agno Memory: local persistent DB path.
12. CrewAI Memory: framework-level wrapper and memory persistence inspection.
13. Zep self-hosted/native automatic extraction split: separate from the current cloud graph harness.
14. Supermemory: hosted API target.
15. Hindsight: local/cloud split after API shape is pinned.
16. Google ADK + Memory Bank: cloud credentials and cleanup discipline.
17. AWS Bedrock AgentCore Memory long-term extraction strategy: IAM execution role, model access, async activation, and cleanup discipline.

## Sources Used For Initial Registry

- Mem0 OSS Python quickstart: https://docs.mem0.ai/open-source/python-quickstart
- Zep quick start: https://help.getzep.com/quick-start-guide
- Zep memory docs: https://help.getzep.com/v2/memory
- Graphiti repository: https://github.com/getzep/graphiti
- Letta Python API: https://docs.letta.com/api/python/
- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangMem docs: https://langchain-ai.github.io/langmem/
- Cognee docs: https://docs.cognee.ai/
- LlamaIndex memory docs: https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/
- CrewAI docs: https://docs.crewai.com/
- Agno memory docs: https://docs.agno.com/memory/overview
- AutoGen memory docs: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/memory.html
- Google ADK memory docs: https://adk.dev/sessions/memory/
- AWS AgentCore Memory docs: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
- AWS AgentCore Memory SDK examples: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/aws-sdk-memory.html
- OpenAI Agents SDK Sessions: https://openai.github.io/openai-agents-python/sessions/
- Supermemory quickstart: https://supermemory.ai/docs/quickstart
- Hindsight docs: https://hindsight.vectorize.io/
