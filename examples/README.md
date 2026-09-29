# Examples

Runs of `eqty-instrument` on real code, for people to read. The agent doesn't use
them, and an install doesn't carry them. Each changed file is here as
`*_before.py` and `*_after.py`, with `changes.diff` between them.

## Mode 1 — LangChain, LangGraph, DeepAgents

The edit these show is the current one. Their manifests were produced by running
the patched code; the skill no longer runs it, and leaves that run to you.

| Example | What it shows |
|---|---|
| [`mode1/deepagent/research_agent`](mode1/deepagent/research_agent) | a DeepAgents research agent, with the manifest it emitted |
| [`mode1/langchain/research_agent`](mode1/langchain/research_agent) | a LangChain research agent |
| [`mode1/langchain/travel_agent`](mode1/langchain/travel_agent) | a LangChain travel agent |
| [`mode1/langchain/travel_chat_ui`](mode1/langchain/travel_chat_ui) | the travel agent behind a chat UI: one handler per conversation |

## Mode 2 — arbitrary Python

Both show the whole current flow, with nothing run: the blind CFG, the data flow
read with pyright, the two joined, the node selection, the patch and the
prediction. Neither has a manifest, because the skill produces none.

| Example | What it shows |
|---|---|
| [`mode2/lora_banking77`](mode2/lora_banking77) | LoRA fine-tuning with `transformers`, `peft` and `datasets`, auto: types `@compute` can't hash, and a model changed in place by training. Start at its [README](mode2/lora_banking77/README.md). |
| [`mode2/pygit`](mode2/pygit) | [benhoyt/pygit](https://github.com/benhoyt/pygit), a small git, auto and HITL: hand-offs through `.git/index` and the master ref, found from the source. Start at its [README](mode2/pygit/README.md). |

Earlier versions of these examples (≤ 0.1.11), from when the skill ran the
example itself and kept the manifest, and a retired llama example, are in the
repository's history.

Third-party code here carries its own license beside it; see [`../NOTICE`](../NOTICE).
