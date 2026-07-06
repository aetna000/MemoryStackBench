from __future__ import annotations

from memorybench.adapters.toy import ToyMemoryAdapter


class LangGraphReferenceAdapter(ToyMemoryAdapter):
    """Temporary local reference target for LangGraph adapter development.

    This is not a real LangGraph benchmark result. It exists so the manifest,
    suite, report, and CI paths can be exercised while the real LangGraph
    implementation is built on the Ubuntu runner.
    """

    capabilities = {
        **ToyMemoryAdapter.capabilities,
        "framework_note": "local reference only, not a framework score",
    }

