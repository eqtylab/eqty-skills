# Vendored from eqty-lineage

Copied verbatim from `eqtylab/eqty-lineage` at commit
`35b65dae9e1f22f0c6a5c9306aab5ea225dfd4d6` (2026-09-17), under the Apache License 2.0
(`LICENSE`). Not maintained here: these are the hand-written originals Mode 1's rules
were distilled from, kept so `references/mode1.md` can cite them by `file:line`.
Unedited, so the line numbers match upstream and the hashes below prove it.

| File | Upstream path | SHA-256 |
|---|---|---|
| `deepagents/research_agent.py` | `examples/deepagents/research_agent.py` | `0d9984a124778ca8233ad1ba4d84600e56c507632ba790688fabafbe55cda7e1` |
| `langchain/research_agent.py` | `examples/langchain/research_agent.py` | `b6ae4f71c2077b04be77e0e92b081b4c8208149ed3dac1489e6fc65ae312f92f` |
| `eqty-lineage-deepagents.README.md` | `packages/eqty-lineage-deepagents/README.md` | `241e385fd8834484932d44bd3ed0aacb91845f20d3df4adb725724f86ad19f72` |
| `eqty-lineage-deepagents.EXAMPLES.md` | `packages/eqty-lineage-deepagents/EXAMPLES.md` | `f72c6fa927c19619d69b48e9d91c7b04ec6099e7d24323acc17a4693a50d5414` |
| `eqty-lineage-langchain.README.md` | `packages/eqty-lineage-langchain/README.md` | `1bea79cc29e62f1753effd685bb59b96b62a5d5241d01314f2f7369c11efce82` |
| `test_interrupt_on.py` | `packages/eqty-lineage-deepagents/tests/test_interrupt_on.py` | `5a1bf3cf0d2ac3ec529720eaadb47a876b60dc3bfb8ab55ce0303841f23ad9ea` |
| `interrupt_guard.py` | `packages/eqty-lineage-langchain/eqty_lineage/langchain/__init__.py`, lines 54–61 only | `6910042722565896a483fe817c2e54c217f142ce33e94effd26e9520cf6bee4e` |
| `LICENSE` | `LICENSE` | `d5baa8feffe25de563abe12bf69be5435c08e95838710ddea286d106b5906d27` |

`interrupt_guard.py` is an excerpt, not a module: the one function
(`_is_graph_control_flow`) that tells a human-in-the-loop pause from a crash. It does
not import or run on its own.
