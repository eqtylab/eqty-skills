# Mode 2 — Which functions become nodes

**Scope:** arbitrary Python codebases (Goal 1, Mode B). Mode A — repos using
LangChain / LangGraph / DeepAgents — is a detection-and-copy problem handled by
the deterministic packages in `eqty-lineage`, and has no "which functions"
question at all. See `mode1.md`.

**Python only.** Both modes target Python and nothing else. Every archetype, example
and tie-breaker below assumes Python semantics — `def`, decorators, `inspect`-readable
source, an AST the skill can walk. An R version of the SDK exists and is explicitly out
of scope for this project.

Six archetypes always, three when they're the named stage, everything else never.

## Always instrument

- **Ingest** — outside world becomes an in-process value: `load_csv(path)`, `fetch(url)`, `db.query(sql)`. This is where trust enters and it's the root of the DAG. Skip it and the graph has no provenance floor.
- **Model / oracle call** — `call_llm()`, `model.predict()`, `embed()`. Nondeterministic and the thing people actually question.
- **Fit / train** — `train()`, `fit()`, `optimize()`. Nondeterministic, produces the artifact.
- **Aggregate / reduce** — `fedavg(updates)`, `merge(shards)`, `vote(results)`. N inputs → 1 output. Highest value per node in the whole graph, because it's the only place lineage genuinely branches.
- **Emit** — `save_model()`, `write_report()`, `publish()`. The artifact leaves the process; this is what someone downstream is holding.
- **Decide** — `should_deploy(metrics)`, `route(request)`, `classify(x)`. A branch taken on data. "Why did it go this way" is the most common audit question, and it's unanswerable without a node here.

## Instrument when it's the named stage

- **Transform** — `clean(df)`, `featurize()`, `tokenize()`. One node per stage a person would name in a sentence, never per column or per field.
- **Evaluate** — `evaluate(model, test)`, `score()`. Yes when the number gets quoted — the metric is usually the whole reason the manifest exists.
- **Validate / gate** — `check_schema()`, `assert_acceptance()`. Yes when the run is allowed to *stop* there. A gate that passed silently is still a claim.

## Never

- Pure functions, no I/O: `to_snake_case`, `_sha256`, `is_valid`, `normalize_column`
- Accessors, `__init__`, properties, dataclass methods
- Loop bodies below the round / epoch / turn level: `forward(batch)`, `process_row(r)`
- Wrappers: `with_retry`, `timed`, `cached` — instrument the inner call
- **Any function created to hold a decorator.** The node belongs on the original definition, in the file it already lives in; a new adapter makes the manifest attest the adapter's source instead of the repo's (`eqtysdk.md` §6.0)
- **Anything the skill itself wrote** — `run_example.py`, scripted backends, stubs, harnesses. Scaffolding is not the program under attestation. If the file is not in the pristine target, it gets no nodes
- Logging, printing, config and CLI parsing
- `tests/`, `conftest.py`

## Worked pass — 12 functions, 6 nodes

```python
def main():
    raw   = load_csv("sales.csv")      # ingest        -> NODE
    df    = clean(raw)                 # named stage   -> NODE
    for c in df.columns:
        df[c] = normalize(df[c])       # inside clean  -> no
    model = train(df)                  # fit           -> NODE
    score = evaluate(model, df)        # measure       -> NODE
    if should_ship(score):             # decide        -> NODE
        save_model(model, "m.pkl")     # emit          -> NODE
    log.info("done")                   # never
```

## Tie-breakers when two candidates are adjacent

- Parent and child both qualify → keep the parent, unless the child is Tier 1. Then keep both and make the nesting deliberate.
- One caller, Tier 2 → fold into the caller.
- Three or more callers → keep it, it's a real unit.
- Returns `None` and writes nothing → not a node, it mutated state. Instrument whatever owns that state.

## Two sanity checks

- **Caption test** — can you write a one-line caption a stranger would understand? *"Trained the model on 4,200 cleaned rows"* is a node. *"Lowercased a string"* isn't.
- **Count test** — number of nodes ≈ number of bullets you'd use to describe the run to a manager. If it's 400, the cut is in the wrong place.
