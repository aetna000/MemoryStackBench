# Local Mem0 Runs

The Mem0 target uses the open-source Python SDK locally. It exercises Mem0 for memory extraction, storage, search, inspection, and deletion, while the adapter uses deterministic response rules over retrieved memories so benchmark output is stable.

## Install

```bash
pip install -e ".[dev,mem0]"
```

## Configure

The default Mem0 OSS setup uses OpenAI for extraction and embeddings, Qdrant on disk, and SQLite history.

```bash
export OPENAI_API_KEY="..."
```

To override Mem0 components, create a YAML config and point the benchmark at it:

```yaml
vector_store:
  provider: qdrant
  config:
    path: /tmp/memorystackbench-qdrant
llm:
  provider: openai
  config:
    model: gpt-4o-mini
    temperature: 0
embedder:
  provider: openai
  config:
    model: text-embedding-3-small
```

```bash
export MEMORYBENCH_MEM0_CONFIG=mem0.local.yaml
```

## Run

```bash
memorybench run \
  --target targets/mem0.yaml \
  --suite suites/seven_sins_v0_1 \
  --out runs/mem0-local

memorybench report --run runs/mem0-local --site site/mem0-local
```

Open `runs/mem0-local/scorecard.html` or `site/mem0-local/index.html`.

## Notes

- The adapter calls `delete_all(user_id=<scenario subject>)` before every scenario to isolate runs.
- Search and `get_all` scope memories with `filters={"user_id": subject_id}`.
- Public scores should pin the exact `mem0ai` version and commit the full target manifest used for the run.
