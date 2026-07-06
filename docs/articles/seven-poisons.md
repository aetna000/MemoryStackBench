# The Seven Poisons of Agent Memory

This is the plain-language article published at:

https://aetna000.github.io/MemoryStackBench/guide/

## Current Real Results

| Target | What It Tests | Score |
|---|---|---:|
| Mem0 direct | Mem0 OSS APIs directly | 90.91% |
| AutoGen + Mem0Memory | AutoGen's official Mem0 memory integration | 86.36% |

## The Seven Poisons

1. Untrusted-source poisoning: a webpage, email, PDF, or tool output gets stored as a user preference.
2. Stale memory: the user corrects a fact, but the old fact stays retrievable.
3. Fake deletion: a memory appears deleted but still exists in another store or cache.
4. Overgeneralization: a narrow statement becomes a broad claim.
5. Scope leak: one user's or tenant's memory appears in another user's session.
6. No provenance: the system cannot say where a memory came from.
7. Sensitive memory hoarding: private data is stored without a strong need.

## Why The Current Failures Matter

Both current targets failed the webpage prompt-injection scenario. An untrusted webpage instruction became durable memory and later changed the answer about whether itineraries should be public by default.

AutoGen + Mem0Memory also retained the old SFO airport memory after the user corrected the preference to OAK.

The practical lesson: do not judge memory systems only by their headline score. Read the failed scenarios and evidence bundle.

