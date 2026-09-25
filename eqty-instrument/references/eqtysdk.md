# Writing evidence with the EQTY SDK

A source-backed guide to registering assets, recording computations, and exporting
manifests with `eqty_sdk`. For reading manifests back, see the companion
`eqty-manifest` skill.

**Source of truth:** [`eqtylab/integrity-py`][upstream], reviewed on 2026-09-21 at
commit `58dd0a21bb6276e44db42c2ce0cc225417fc466a` from `main`. All upstream links below
are pinned to that commit. Its [changelog][changelog] lists **2.4.2** as the newest
stable release; `main` is a source snapshot, not a promise about every installed
wheel. Check your installed version before relying on version-specific behavior.
Runtime claims marked in §2, §4, §6, and §8 were also spot-checked against the
published `eqty_sdk==2.4.2` wheel.

**Evidence policy:** API behavior below comes from upstream implementation, tests,
and examples. Where upstream prose is less precise than the implementation, this
guide says so. Advice marked **Recommendation** is an application design choice,
not an SDK requirement. Earlier FLARE/pygit experience does not override upstream.
Section numbers are retained for existing references, but several former rules
have been corrected; see §14.

## 1. The mental model

The SDK records provenance statements about content, identities, computations,
metadata, and other relationships. A context groups statements for export or
service registration. A manifest includes statements, JSON-LD contexts, and
available referenced blobs. [Sources: public API][public-api], [export implementation][contexts].

- An asset's **CID** identifies its hashed representation. Names, asset types,
  and other metadata are registered separately; they do not change that CID.
- Equal serialized bytes under the same hashing/codec scheme have equal CIDs.
  Equal Python meanings do not necessarily serialize identically. Different
  codecs can identify different representations of the same logical content.
- A computation statement links input and output CIDs. The decorator records
  these around a function call; the builder records relationships you declare.
- A signature authenticates an assertion. It does not independently prove that
  the claimed program ran correctly, that every dependency was captured, or that
  the signer was authorized for your application.

Content identity is different from execution identity. Two runs may legitimately
produce the same asset CID while having separate computation statements. Do not
assume the graph must have unique producers, one connected component, or no cycles:
the SDK does not enforce those application-level constraints. [Sources: assets][assets],
[computation statements][computation-statements], [verification][verification].

## 2. Setup and signing identities

A complete setup using a stable local signer:

```python
from pathlib import Path
import eqty_sdk as sdk

context = sdk.Context.new("example-run")  # Choose the context used by default.
cfg = sdk.init(default_context=context, custom_dir=Path(".eqty-example"))
signer = sdk.Signer.load_or_create(name="example-signer")  # Reuse the persisted key.
sdk.set_active_signer(signer)  # Subsequent registrations use this active signer.
```

Order matters:

- **`init()` comes first.** Hashing, registering, and even creating or loading a
  signer need the initialized config; `Signer.load_or_create()` before `init()`
  raises `RuntimeError: Config not initialized`. Signers are stored under the SDK
  directory (`<custom_dir>/signers/`), so a signer belongs to one SDK directory.
- **`init()` only takes effect once per process.** A second call logs a warning and
  returns the existing config; its `default_context` and `custom_dir` are ignored.
- **An active signer is required for every statement**, including with
  `_skip_proof=True`. Registering before `set_active_signer()` raises
  `RuntimeError: No active signer available`. The active signer is in-memory only;
  each new process must set it again.
- Creating a `Context` before `init()` is supported when it is passed as
  `default_context`, as in upstream examples. `init()` persists that context;
  other contexts are persisted only when created after `init()`.

[Sources: `init` and config][config], [signer implementation][signers],
[configuration docs][config-docs], [context example][context-example].

| API | Behavior at the reviewed commit |
|---|---|
| `Signer.new(...)` | Generate and persist a fresh key; an existing explicit name raises `ValueError`. |
| `Signer.load(name)` | Load an existing signer; missing names raise `LookupError`. |
| `Signer.load_or_create(name=..., algorithm=...)` | Reuse the named signer, or create it; the algorithm applies only when creating. |
| `Signer.new(name=..., _load_if_exists=True)` | Legacy reuse option; emits a `DeprecationWarning`. Prefer `load_or_create`. |
| `Signer.from_private_key(algorithm, private_key, ...)` | Import a base64-encoded private key; `algorithm` must match the key. |
| `Signer.auth_service(...)` / `Signer.vcomp_notary(...)` | Service-backed signers with their own setup requirements. |

Supported local algorithms are `ED25519`, `SECP256K1`, and `SECP256R1`; `new()`
defaults to Ed25519. `signer.did_key` exposes its DID. An unnamed signer is stored
under its DID string. `load()` and `load_or_create()` were added in **2.3.0**.
`set_active_signer()` reloads the key by name from the current SDK directory, so it
fails for a signer persisted elsewhere. [Sources: signer implementation][signers],
[signer tests][signer-tests], [changelog][changelog].

**The SDK does not impose one identity per process.** `set_active_signer()` loads
the selected signer and updates process-global configuration. Sequential switching
is supported by that implementation. The active signer is not scoped to a Python
function, context, or async task. Do not infer safe concurrent identity isolation
from the setter. [Sources: signer implementation][signers], [global configuration][config].

**Recommendation:** separate processes and SDK directories are useful isolation
choices for mutually distrusting participants. Use expected-DID checks when your
application requires authorization. These are application controls, not prerequisites
for every SDK script. Keep signer changes out of overlapping work that could sign
under the wrong global identity.

## 3. Asset names, identity, and types

### 3.1 Names are optional metadata

`from_object()`, `from_path()`, and `from_cid()` supply a default name of
`<asset type>-<last four CID characters>` when `name` is omitted. Both of these
calls are valid:

```python
rows = sdk.Dataset.from_object([1, 2, 3])  # SDK-generated name.
labeled_rows = sdk.Dataset.from_object([1, 2, 3], name="Training rows")
assert rows.cid == labeled_rows.cid  # Naming does not alter content identity.
```

**Recommendation:** use descriptive names where they help a reader. A generated
name is not an integrity failure. [Sources: asset factories][assets], [default-name tests][name-tests].

### 3.2 Shared naming functions are optional

The SDK does not require a helper per asset category, a global naming registry, or
one name per CID. Re-registering a CID can attach additional metadata statements.
The SDK repository does not define a universal Explorer policy for choosing among
multiple labels; do not claim the viewer selects one arbitrarily as an SDK contract.

**Recommendation:** reuse an asset or consistent metadata when that makes a workflow
clearer. Inline `Document.from_object(message, name="Commit message")` is normal SDK
usage. **When instrumenting existing code, add no naming helpers** (§6.10):
register each asset inline where its data is read or written, and accept that the
same bytes may carry different labels at different sites. [Sources: asset registration][assets],
[quick-start example][quick-start], [metadata implementation][metadata].

### 3.3 Names do not create distinct content

Different names, types, sites, or round numbers supplied as metadata do not split
identical content into separate CIDs. Such metadata can describe how content was
used, but must not be treated as content identity. The SDK permits multiple labels
and types for the same CID. [Sources: asset factories][assets], [type tests][type-tests].

### 3.4 Separate an artifact from an observation about it

Repeated identical results may share a CID. Their computations can record who
produced or used them. You do not need to modify an artifact just to make the graph
look different.

**Recommendation:** if the artifact is an observation whose meaning includes site,
time, or run, include those fields in that observation's payload. If they describe
the execution instead, use computation metadata. Adding a discriminator changes
what is being hashed; it is a modeling choice, not an SDK requirement.
[Sources: computation builder][builder], [asset serialization][assets].

### 3.5 `Custom` is a supported type

The decorator uses `Custom` for many ordinary inputs and outputs. Upstream explicitly
supports both `Custom.from_object(value)` and a domain-specific `asset_type` label.
Neither a default `Custom` nor a generated name makes a manifest invalid.

```python
custom = sdk.Custom.from_object({"kind": "template"})  # Supported default type.
table = sdk.Custom.from_object(
    {"columns": ["customer_id"]},
    asset_type="FeatureStoreTable",  # Optional custom label.
    name="Customer features",
)
```

**Recommendation:** use a built-in type when it accurately describes your content.
Types describe meaning; they do not select a different serializer or validate a
payload's schema. Display icons are a viewer concern. `computation_type` is also
optional metadata, not a required or enforced taxonomy. [Sources: asset docs][asset-docs],
[asset implementation][assets], [metadata implementation][metadata].

### 3.6 Type catalogue and constructors

The reviewed source exports 21 built-in types:

`Agent`, `Benchmark`, `BenchmarkResult`, `Binary`, `Certificate`, `Code`,
`Configuration`, `Credential`, `Custom`, `Database`, `Dataset`, `Document`,
`Guardrail`, `Media`, `Model`, `Prompt`, `Reasoning`, `Skill`, `SystemPrompt`,
`Token`, `Tool`.

Manifest labels include `Benchmark_Result` and `System_Prompt` for the Python
classes `BenchmarkResult` and `SystemPrompt`. [Source: `AssetType`][assets].

| Constructor | What it does | `.value` contains |
|---|---|---|
| `Type.from_object(obj, ...)` | Serialize, hash, and register the object. | Original object. |
| `Type.from_path(path, ...)` | Hash and register file or directory content. | Resolved `Path`, not loaded file bytes. |
| `Type.from_cid(cid, ...)` | Register a typed reference and metadata for an existing CID. No content hashing or fetching. | CID object. |
| `Type.with_context(ctx).from_*` | Same constructors with an explicit context. | As above. |

Use factory methods rather than direct typed-asset constructors. Use an SDK `CID`
object for `from_cid`, for example `sdk.CID(cid_text)`; a plain string raises
`TypeError`. `from_cid()` has no `_store` parameter, so a `_store=...` keyword
passed to it is recorded as ordinary metadata rather than honored.

**`from_cid()` does register statements** unless registration is explicitly skipped.
It neither fetches the referenced content nor imports its earlier provenance. Hashing
the same bytes again does not, by itself, create a different content CID.
[Sources: factory implementation][assets], [public constructor docs][asset-docs].

Additional non-control keywords become metadata. Asset attribute lookup first tries
the underlying value, then metadata; avoid conflicting attribute names. Attribute
*assignment* on an asset (other than SDK internals) is forwarded to the wrapped
value, so `asset.label = "x"` does not change metadata and fails on values such as
`str`. SDK controls such as `_store`, `_skip_proof`, and `skip_registration` are not
ordinary domain metadata. A registered asset exposes the IDs it created in
`asset.statement_ids`: data and metadata statements, plus one credential each
unless proofs are skipped.

### 3.7 Directory hashing

`from_path(directory)` uses the SDK's directory-hashing implementation and produces
an iroh collection CID. Hashing honors configured ignore rules, including hidden
files, `.gitignore`, and symlink settings. Collection identity represents included
files and their layout, not simply a concatenation of file contents.

The Rust binding always stores the collection and its metadata blobs. File-content
blobs are stored only when the effective storage setting is true. Thus `_store=False`
on a directory does **not** mean no blobs or no filename information are retained.
[Sources: path hashing][hashing], [collection tests][cid-tests], [ignore-rule configuration][config-docs].

`cfg.set_cid_ignore_rules(...)` replaces all three rules at once: omitted arguments
revert to their defaults rather than keeping the current value. The rules are saved
to `config.toml` in the SDK directory and so persist across runs. [Source: config][config].

**Recommendation:** record ignore settings when reproducibility matters. Treat exact
collection byte layout as an implementation detail rather than maintaining a second
handwritten encoder based on one observed fixture.

## 4. Storage and content availability

Hashing, registering statements, retaining bytes, and exporting are separate actions.

| Setting or operation | Effect |
|---|---|
| `cfg.set_store_all_blobs(True/False)` | Set the default for retaining newly hashed preimages. Saved to `config.toml`, so it persists for later runs using the same SDK directory. The initial default is `False`. |
| `_store=True` | Retain the bytes this operation hashes. |
| `_store=False` | Override the default for this operation; see the directory exception in §3.7. |
| `_store=None` / omitted | Use the configured default. |
| `from_cid(cid)` | Register a reference; provides no preimage and has no storage-control parameter. |
| `context.export(path)` | Resolve available referenced blobs and serialize the manifest. |

`_store=True` writes to the **local blob store**, not directly to a manifest. Export
later includes resolvable referenced content. A decorator's storage setting applies
to content it hashes; it does not retroactively store or remove the content of an
already constructed asset. `_store=False` also does not erase an existing blob with
the same CID. [Sources: hashing][hashing], [decorator implementation][compute],
[export implementation][contexts], [storage tests][compute-tests].

**Metadata is always stored and exported.** Every metadata statement writes its
JSON (names, descriptions, and every extra keyword) to the blob store regardless
of `_store` or the global setting. Export then includes it. `_store=False` keeps
the asset's content out of the manifest, not its metadata. [Source: metadata statements][metadata-statements].

A CID-only reference is valid. Missing preimages mean a reader cannot independently
recompute that asset's content hash until the bytes are provided; they do not make
all of its surrounding statements unverifiable. Metadata and credentials have
separate verification requirements.

**Recommendation:** retain shareable content when the recipient needs to inspect it.
For sensitive or large artifacts, register with `_store=False` or use an existing
CID. Neither content addressing nor base64 encoding is encryption. Review the actual
export, including metadata and any previously retained content, before sharing it.

## 5. Computations with the builder

Use the builder to declare inputs, outputs, and optionally computation identity
explicitly. It can describe a Python operation, an external job, or other work;
it does not execute or validate that work for you. [Source: builder][builder].

```python
input_asset = sdk.Dataset.from_object({"rows": 3}, name="Input summary")
output_asset = sdk.Document.from_object({"accepted": 3}, name="Result summary")

computation = (
    sdk.Computation.new(name="Validate batch", _store=True)
    .add_input_cid(input_asset.cid)  # CID object, not a plain string.
    .add_output_cid(output_asset.cid)
    .set_computation_object({"operation": "validate-v1"})  # Optional description.
    .finalize()  # Write the computation and its metadata statements.
)
```

- `add_input_cid` / `add_output_cid` accept a `CID` or list of CIDs.
- `add_input_path` / `add_output_path` accept a path or supported homogeneous list
  of paths and hash their content.
- `add_input_object` / `add_output_object` serialize objects; a Python list here
  means **multiple objects**, one CID per element. Use a preconstructed asset to
  represent a list as one payload.
- `set_computation_cid` accepts one CID; `set_computation_path` accepts one path;
  `set_computation_object` serializes one object. These set a single computation
  reference, unlike the accumulating input/output methods. `set_computation_cid`
  silently ignores a non-`CID` argument instead of raising.
- A missing input/output path raises `UsageError`; a wrong argument type for the
  CID/path methods raises `ValueError`.
- Object/path methods compute CIDs but do not construct named, typed `Asset`
  registrations for every input/output. Construct assets separately if needed.
- `Computation.with_context(ctx).new(...)` binds the builder explicitly. Otherwise
  it uses the active `graph_context`, then the configured default.
- Extra metadata passed to `new()` describes the computation. There is no required
  `name`, `site`, `round`, or `computation_type` schema.

`finalize()` writes the computation statement and metadata and normally issues
credentials. Proof generation is suppressed by `_skip_proof=True`, or, when
`_skip_proof` is not passed, by the environment variable `EQTY_SKIP_PROOF=true`.
`EQTY_TIMESTAMP` overrides the timestamp on new statements.
The builder and decorator always set `operatedBy` to the active signer's DID, except
with a VComp notary signer, whose operator comes from the notary. An explicit
operator or `executedOn` requires the advanced API
`eqty_sdk._rust.statements.add_computation_statement(..., operated_by=..., executed_on=...)`. Earlier hashing or asset registration may already have side effects
before `finalize()`; an unfinished builder does not mean nothing was written.
[Sources: builder][builder], [computation registration][computation-statements].

**Standalone assets export without a packaging computation.** Upstream's 2.4.0
changelog records the export fix, and current retrieval starts with directly linked
statements even when a context contains no computations. Do not invent a computation
solely to make a document export. A packaging computation is useful when it honestly
describes a packaging operation. [Sources: changelog][changelog], [statement retrieval][indexer].

## 6. Recording calls with `@compute`

The decorator can preserve ordinary Python arguments and results:

```python
@sdk.compute(metadata={"name": "Add numbers"}, _store=True)
def add_numbers(left, right):
    return left + right  # Returning a plain value is supported.

result = add_numbers(2, 3)
assert result == 5  # The caller receives the original result, not an auto-wrapped Asset.
```

On each call, the wrapper creates a `Compute` instance, captures source, converts
inputs for lineage, executes the function, converts outputs for lineage, and records
the computation. It forwards the original arguments to the function and returns the
original result. Asset conversion is for recording; it does not transparently rewrite
the function's runtime types. [Sources: decorator wrapper][decorator], [Compute][compute].

### 6.0 Source capture and placement

With `@compute`, `inspect.getsource(func)` runs **when the decorated function is
called**, not when the decorator is applied. Every call therefore re-reads the
source and registers a `Code` asset named after the function, using the docstring
as its description. Direct construction of `Compute(func)` captures source at
construction. Unavailable source can raise an inspection error; REPL/generated/native
callables need particular care.

Positional inputs are converted **before** the function runs, and outputs **after**
it runs. An unsupported input type raises before execution. An unsupported or
`None` output raises after the function's side effects have already happened.

The captured source becomes a `Code` input. It is the inspected function's text,
not a complete dependency closure or a measurement of the executing interpreter.
A function can depend on helpers, imports, globals, files, or network responses
that this source blob does not include. [Sources: decorator][decorator], [Compute initialization][compute].

The captured text includes the `@compute(...)` lines themselves. A function
decorated in its own file therefore never hashes to its undecorated upstream text,
and every edit made to fit the decorator also changes the recorded code.

**Recommendation:** decorate the function whose boundary you intend to record.
Wrappers, new modules, and additional `Code` assets are not prohibited by the SDK.
If you record a wrapper, its source is what is captured; include other relevant
code/artifacts explicitly when the provenance claim requires them. Test the entry
points users actually call. Edit existing functions only within the rules of §6.10.

### 6.1 Positional arguments and keyword arguments

Automatic input collection examines positional `args`, not `kwargs` or omitted
defaults. Passing the same parameter by keyword can therefore remove it from the
recorded inputs. Type annotations do not change this behavior.

The implementation pairs declared parameter names with positional arguments using
`zip`; it is not full signature binding. Extra `*args` can be missed. Prefer the
builder for a boundary needing explicit, complete input enumeration.
[Source: `Compute.__args_to_assets__`][compute].

```python
@sdk.compute(metadata={"name": "Format report"})
def format_report(report, *, access_token=None):
    return report.upper()  # The keyword-only token is not automatically captured.
```

**Recommendation:** keep secrets out of automatic capture, output, source literals,
and metadata. Keyword-only parameters can prevent accidental positional capture,
but they are not a general redaction system. `_store=False` suppresses new preimage
retention, not the hash or all metadata; hashes of predictable secrets can still
reveal information.

### 6.2 Return values

Plain supported values are valid outputs. The sync/async function caller receives
its original result; the recording path separately constructs assets:

- An existing `Asset` is reused.
- A scalar/dict becomes a `Custom` asset by default, with an SDK-generated name.
- `metadata={"output_type": "dataset"}` or `"model"` selects that output type for
  implicitly constructed assets. Other values fall through to `Custom` in this
  implementation; an existing returned asset retains its type.
- A top-level `None` raises `UsageError` after the function has run. Nested `None`
  entries in output lists/tuples are omitted from the recorded output CIDs.
- Lists and tuples have special handling; see §6.4.

Explicitly returning a named asset is an option when downstream code wants that
asset or more descriptive metadata. It is not required. [Sources: output conversion][compute],
[basic workflow example][basic-example], [None-return tests][none-tests].

### 6.3 Input conversion

| Positional input | Recorded representation |
|---|---|
| Existing `Asset` | Its CID. |
| List containing only assets | Each asset's CID; an empty list contributes no data inputs. |
| Object with `to_eqty_asset()` | The asset returned by that method. |
| `None` | No data input. |
| Other value accepted by §7.1 | A `Custom` asset named after the parameter. |
| `tuple`, `bytes`, `set`, or any other value §7.1 rejects | `TypeError` **before** the function runs. |

A raw `Path` is serialized as path text by this version; it is not a request to
read the file. See §6.7. A tuple is not a supported positional input, even a tuple
of assets; pass a list of assets instead. [Source: argument conversion][compute].

**Recommendation:** preconstruct a typed asset only when explicit type/metadata or
content selection is useful. If you pass that asset, the function receives it and
may need `.value`. A `to_eqty_asset()` input adapter can instead preserve a natural
runtime object. Ordinary strings and numbers do not need wrappers merely to be valid.

### 6.4 Collections and chaining

The input and output rules are asymmetric:

| Value | As a positional input | As an output |
|---|---|---|
| Nonempty list of ordinary values | One serialized list asset. | Recursively converted into separate output assets. |
| List of assets | Individual input CIDs. | Individual output CIDs. |
| One asset wrapping a list | One input CID. | One output CID. |
| Empty plain list | No data input. | One asset containing `[]`. |
| Tuple | `TypeError` before execution. | Recursively converted into output assets. |
| `bytes` / `set` | `TypeError` before execution. | `TypeError` after execution. |

Returning `["a", "b"]` and passing that raw list positionally into the next
function therefore does not identify one shared list asset across the boundary.
Choose either an asset containing the whole list or a list of explicit assets,
according to the intended relationship. This is a content-modeling issue, not a
requirement to turn every return value into an asset. [Source: collection conversion][compute].

### 6.5 Supported serialization and adapters

See §7.1 for exact serialization rules. Raw `bytes`, `set`, `tuple`, and `None`
are not supported by `from_object()`'s general serializer; each raises `TypeError`.
Decorated output tuples work through the separate recursive conversion described
above, but their elements must still be serializable.

Two extension mechanisms have different purposes:

- `serialize_for_hashing()` returns bytes and is used by `from_object()` and
  builder object methods for objects reaching that serializer branch.
- `to_eqty_asset()` returns an `Asset` and is recognized for **decorator inputs
  only**. It is not an output protocol or a general `from_object()` hook.

A byte payload does not have to be written to disk to become an asset:

```python
payload = b"packed content"
cid = sdk.get_cid_for_bytes(payload, _store=True)  # Hash and retain this exact snapshot.
artifact = sdk.Binary.from_cid(cid, name="Packfile")  # Register type and metadata.
```

Alternatively, use `Binary.from_path()` for a file or a custom object exposing
`serialize_for_hashing()`. For sets, define the ordering/encoding your application
intends; converting to a sorted list is suitable only when its elements are sortable.
[Sources: serialization][assets], [byte hashing][hashing], [adapter docs][asset-docs].

### 6.6 Identifiers and artifacts are different claims

Hashing a SHA-1 string or URI records that identifier's bytes. It does not hash or
validate the artifact it names. This is valid when the identifier itself is the data
being processed, such as a ref lookup result.

**Recommendation:** use `from_path()` or a precomputed content CID when the claim is
about artifact bytes; attach an identifier as metadata when useful. Hashing an
identifier does not inherently create a cycle. Cycles depend on the actual recorded
input/output relationships. [Sources: hashing and serialization][assets], [builder][builder].

### 6.7 Paths and file content are different claims

Both `from_object("file.txt")` and `from_object(Path("file.txt"))` hash path text
in this version. `from_path("file.txt")` hashes file content. Bare positional paths
use the former serialization behavior unless adapted explicitly.

```python
path_text = sdk.Configuration.from_object("weights.bin")  # Commit to the path string.
weights = sdk.Model.from_path("weights.bin", _store=False)  # Commit to file content.
```

For path-backed assets, `.value` is the resolved path. `str(asset)` does not read
that file. For CID-backed assets, `.value` is the CID; no remote download occurs.
[Source: asset factories and value access][assets].

### 6.8 Selection steps and repeated content

The decorator records returned assets as outputs, including assets that existed
before the call. Returning selected existing assets is supported. It may make a
content-level graph ambiguous about creation versus selection, but it is not an
SDK error and does not necessarily create a cycle.

**Recommendation:** when that distinction matters, return a decision/selection
record, or use an explicit computation/association model. Describe the operation
accurately instead of claiming every output was newly created. [Sources: output handling][compute],
[association example][association-example].

### 6.9 Async functions and streaming

Async functions are awaited, then their results are recorded using the normal
output conversion. Async generators use the stream API: chunks are yielded to the
caller and the accumulated stream is finalized after iteration completes. Supported
chunks include strings, bytes, numbers, and JSON-encodable dict/object forms; this
is a different path from ordinary `from_object()` serialization.

After a complete iteration, the stream is registered as a `Custom` asset named
`<computation name>-stream`. A consumer stopping early or an exception before
stream completion can prevent finalization. Do not claim a completed stream
computation before consumption finishes.

**Synchronous generators are not supported.** `Compute.__call__` has no
synchronous-generator branch. Decorating one makes each call fail with
`TypeError: Unsupported data type for hashing: <class 'generator'>`. The generator
object is created but its body never runs.
[Sources: async implementation][compute], [async example][async-example].

### 6.10 Editing code to instrument it

The manifest attests the code that ran. When the instrumented file is the one
that actually runs and ships, "original code + EQTY code" is honest provenance for
that program. Editing is therefore allowed, within three rules:

1. **Preserve behavior.** Every caller, including the program's own command line,
   gets the same results and side effects as before. No new files written into
   the user's data, no changed exit codes, no instrumentation switched off.
2. **Record only what really happened.** Every recorded input was actually read
   or received by that step, and every recorded output was actually produced by
   it, hashed from the bytes in hand at that moment.
3. **No naming helpers.** Do not add functions whose job is to build, name or
   type assets. Register assets inline where the data is read or written. Names
   and types are optional (§3.1–§3.5).

**`@compute` failure modes and the allowed fixes:**

| Pattern in the original function | Failure | Allowed fix |
|---|---|---|
| Returns `None` | `UsageError` after the function has run | Return a record of an output the function really produced, such as an asset of the file it just wrote, or a value it already computed. Only where no caller uses the `None` |
| Returns a `set`, `bytes`, or other value §7.1 rejects | `TypeError` after the function has run | Return a supported equivalent only if every caller behaves identically; otherwise record with the builder inside the function (below) and leave it undecorated |
| Takes a positional `tuple`, `set`, `bytes`, or other unsupported value | `TypeError` before the function runs | Same: convert at the call site only if behavior is identical; otherwise use the builder |
| Is a synchronous generator, or has no retrievable source | `TypeError` / `OSError` | Use the builder, or report a gap |

**Data that moves through files or shared state.** `@compute` sees only
positional arguments and return values. When one step writes a file and a later
step reads it, as `add` writes `.git/index` and `commit` reads it, use the
`Computation` builder **inside the function that does the reading or writing**.
Hash the file's bytes at the point of use, and record them as that step's input or
output. The equal bytes give equal CIDs, so the edge forms because the data
actually flowed.

**Not allowed:**

- changing a parameter list: adding a parameter, or changing a parameter's type to
  an asset, just so the decorator records it;
- adding a parameter or return value that the function does not use, to create an
  edge. The edge would be asserted, not observed;
- changing a return value that callers use, unless every caller behaves identically;
- naming helpers or any other new asset-building functions;
- recording the same content under a different identity to make the graph look
  cleaner.

What remains unrecordable is reported as a gap: path-text or identifier inputs,
`Custom` nodes with generated names, missing edges.

[Sources: decorator][decorator], [output conversion][compute], [serializer][assets],
[builder][builder].

## 7. Reproducing content identifiers

### 7.1 `from_object()` is not universally JSON

The serializer checks these branches in order:

| Value | Bytes hashed |
|---|---|
| `str` | UTF-8 text, without JSON quotation marks. |
| `int` / `float` | `str(value).encode("utf-8")`; booleans also enter this branch. |
| `list` / `dict` | Default `json.dumps(value).encode("utf-8")`. |
| `Path` / `os.PathLike` | `os.fspath(value).encode("utf-8")`, not file content. |
| Object with `serialize_for_hashing()` | Its returned bytes. |
| Object with `.model` | JSON of `.model.state_dict()`, if serializable. |
| Object with `__dict__` | JSON of that dictionary, if serializable. |
| Anything else | `TypeError`. |

Consequently, a string `"1"` and integer `1` serialize to the same bytes. Dict
insertion order, separators, and encoding can affect a CID. This is not canonical
JSON or a type-preserving encoding. [Source: `serialize_for_hashing`][assets].

`get_cid_for_bytes()` hashes raw bytes using the SDK's BLAKE3 raw-binary scheme.
`get_cid_for_json()` separately canonicalizes JSON using JCS and a different codec;
it is not interchangeable with `from_object(dict)`.
[Source: hashing functions][hashing].

**Recommendation:** reproduce the exact emitted bytes when checking an existing CID.
If you need canonical application serialization, define it explicitly before hashing;
changing to sorted keys is a deliberate representation change, not inherently wrong.

### 7.2 Binary and model representations

Use exact artifact bytes when the claim concerns a particular saved file. A pickle,
checkpoint file, or other binary artifact can legitimately be hashed as bytes.
If you need equivalent model states across frameworks or processes to share an
identity, define a stable encoding of names, shapes, dtypes, and tensor bytes. That
normalization is application logic, not a built-in SDK guarantee.

`Model.from_object()` does not make arbitrary model objects automatically portable.
Its fallback serializer can fail on tensors or other non-JSON values. Use a path,
explicit byte representation, or supported serialization hook. [Source: serializer][assets].

### 7.3 Mutable inputs and snapshots

`from_path()` is not an atomic snapshot guarantee. In the reviewed file-hashing
implementation, the file is hashed and then read again if storage is requested.
A source can change between those reads, or between registration and execution.
[Source: path hashing implementation][hashing].

**Recommendation:** freeze inputs when consistency matters. For an in-memory
snapshot, read once, validate and process those same bytes, then use
`get_cid_for_bytes(snapshot, _store=...)` plus a typed `from_cid()` registration.
Comparing a returned CID to an expected CID is useful but does not alone eliminate
races caused by reopening the original file.

## 8. Contexts, export, import, and merge

- `Context.new(name)` creates a root with a new UUID.
- `Context.with_parent(parent).new(name)` creates a child context.
- `Context.from_uuid(uuid.UUID(text))` takes a **standard-library `uuid.UUID`**.
  `sdk.UUID` and plain strings raise `TypeError: Expected a uuid.UUID instance`.
  It is used to match an existing Governance Studio project ID. It does not check
  that a populated local or remote project exists; if the SDK is initialized, it
  just records a local context with that ID.
- Calling `.new(...)` on a context *instance* raises `TypeError`. Use
  `Context.new(name)` or `Context.with_parent(ctx).new(name)`.
- Creating a context does not make it the default. Supply it to
  `init(default_context=...)`, use `.with_context(ctx)` factories, or use
  `graph_context(ctx)` (imported from `eqty_sdk.context`).
- `graph_context()` uses a Python `ContextVar`. Asset factories and
  `Computation.new()` read it. **`@compute` only half-honors it:** without an
  explicit `ctx=`, the decorator's code, input, and output assets go to the active
  `graph_context`, but the computation statement and its metadata go to the
  **default context**. Exporting the `graph_context` context then contains no
  computation. The same applies to async-generator streams. Always pass
  `ctx=` to `@compute` when not using the default context.

```python
from eqty_sdk.context import graph_context

run = sdk.Context.new("run-1")

@sdk.compute(metadata={"name": "Step"}, ctx=run)  # Required for the computation to land in `run`.
def step(value):
    return value + 1

with graph_context(run):  # Enough for assets and the builder, not for @compute.
    step(41)
```

Other context operations: `ctx.register(service, delete_blobs=..., delete_statements=...)`
uploads the context and its ancestors to a service; `ctx.delete()` removes a
childless context's local statements; `ctx.delete_tree()` also removes descendants.
Neither delete removes local blobs; `sdk.purge_blob_store()` and
`sdk.purge_statement_store()` clear the whole SDK directory's stores.

[Sources: context implementation][contexts], [Python graph context][graph-context],
[decorator implementation][compute], [builder][builder].

```python
merged = sdk.Context.new("combined-run")  # A destination context in the initialized store.
merged.import_manifest(Path("participant-a.json"))
merged.import_manifest(Path("participant-b.json"))
merged.export(Path("manifests/combined.json"))  # Includes directly linked statements.
```

Import stores blobs and links imported statements to the destination context; it
does not re-sign every assertion as the importer. A shared destination context is
not evidence that the imported computations have connected dataflow.

**Import is not a substitute for verification or authorization.** The import path
parses/stores statements; it does not call `verify_statement()` or `verify_vc()` for
each one. It also does not persist the manifest's supplied JSON-LD context documents
separately. Preserve custom contexts when round-tripping manifests that require them.
[Source: import implementation][contexts].

Export creates missing parent directories and writes the destination file directly.
The manifest has four top-level keys: `version`, `contexts`, `statements` (keyed by
statement ID), and `blobs` (base64, keyed by CID). Setting
`EQTY_INCLUDE_MANIFEST_CONTEXT=false` omits the JSON-LD `contexts`. When the
active signer is a VComp notary, its credentials and DID blobs are added to every
export. **Recommendation:** use a temporary file and atomic replacement if readers
must never observe partial output.

Export reaches **up** the context tree, not down. Exporting a parent does not
include statements registered only in its children. For the inputs and outputs of
a child context's computations, export also pulls related data/metadata statements
from ancestor contexts. Export each context you need. [Sources: export][contexts],
[retrieval][indexer], [parent/child example][context-example].

## 9. Packaging evidence

### 9.1 Nested manifests are not forbidden by the SDK

A manifest file can be registered as a document. The SDK repository does not establish
a blanket rule that Explorer crashes on nested manifests. The previous blanket ban
was based on a project-specific observation and is not a verified SDK limitation.

**Recommendation:** choose between importing statements, attaching a manifest as an
artifact, and recording a compact descriptor according to what the recipient needs.
For a descriptor, include the actual manifest CID if the intent is to commit to its
content. A descriptor CID alone identifies the descriptor, not every external file
it mentions. [Sources: path-backed registration][assets], [manifest import][contexts].

### 9.2 References versus registrations

A builder input CID records a relationship without itself adding a typed asset's
metadata. `Document.from_cid(cid, name=...)` additionally registers that asset and
metadata but still supplies no preimage. `Document.from_path(path, _store=True)`
hashes and retains the content. Select the level your package actually needs;
none is universally the only valid form. [Sources: builder][builder], [assets][assets].

### 9.3 Validation is application-specific

**Recommendation:** before endorsing participant work, check the expected signers,
required computations, relevant input/output CIDs, and available content. A successful
import alone is insufficient. Expected counts and allowed computation names depend
on the workload, not the SDK.

Do not universally require `registeredBy == operatedBy`: the advanced statement API
accepts an explicit operator, and VComp notary signers supply their own, so the
operator may differ from the registering signer. Statements from the builder and
decorator with a local signer have equal values. Authorize each role according to
the statement semantics. [Source: computation statements][computation-statements].

## 10. Privacy and storage policy

The SDK supports both content-bearing manifests and CID-only provenance. It does not
require all datasets to become summaries or forbid publishing model weights.

**Recommendations:**

- Decide which content and metadata the recipient may receive before enabling storage.
- Use `_store=False` for sensitive preimages; account for existing stored blobs and
  directory metadata (§3.7–§4).
- Treat every name, description, and metadata keyword as published: metadata JSON
  is always stored and exported, whatever `_store` says (§4). With `@compute`, the
  function's docstring becomes the `Code` asset's description.
- `_store` does not prevent the function source from being hashed or registered.
  With storage enabled, the source text itself is exported.
- Do not treat a dataset descriptor as a commitment to the dataset unless it includes
  a digest/CID of the actual data. Counts and labels alone identify only that summary.
- Keep credentials and private keys out of captured arguments, outputs, source,
  descriptions, and other metadata. Keyword exclusion only covers one capture route.
- Review the exported content against your own disclosure policy. Rules such as
  banning every list longer than 32 values are project-specific, not SDK restrictions.

[Sources: upstream storage guidance][quick-start-docs], [hashing][hashing], [input capture][compute].

## 11. Verification and completion checks

Cryptographic validity, content availability, and workflow correctness are separate
checks. Names, graph shape, and asset types can improve readability without proving
that the recorded relationships match execution.

**Recommended checks for an instrumented workflow:**

- [ ] Record the SDK version and relevant storage/context configuration.
- [ ] Run the entry points whose behavior the instrumentation claims to cover.
- [ ] Inspect exported inputs/outputs and source against the actual work, including
      keyword arguments and implicit dependencies omitted by automatic capture.
- [ ] Verify statement IDs and applicable credentials; bind each credential to the
      expected subject and authorize its issuer/operator separately (§13.3).
- [ ] Check available blobs with their appropriate CID codec. Report unavailable
      content explicitly; do not claim its bytes were verified.
- [ ] Assess missing steps, repeated content, and connectivity against the intended
      workflow. Do not reject valid `Custom` assets, generated names, repeated labels,
      disconnected components, or cycles merely because the old guide banned them.
- [ ] Review disclosure and prove only the scope actually checked.

Upstream verifier APIs do not validate application truth or cover every blob and
credential format with one call. See §13.3 and [upstream verification docs][verification-docs].

## 12. Corrections at a glance

| Concern | Accurate guidance |
|---|---|
| No explicit asset name | SDK supplies a default; descriptive names are optional. |
| Multiple names for one CID | Multiple metadata statements are allowed; choose conventions for clarity. |
| Many asset-construction helpers | Optional refactoring, not an instrumentation requirement. |
| `Custom` asset | Supported category, including implicit decorator values. |
| Plain return value | Supported; original result reaches the caller. |
| Top-level `None` return | Decorator raises after execution. Use a suitable result or explicit builder. |
| Keyword input | Not automatically captured; model it explicitly when needed. |
| Identifier/path text | Valid as text; does not commit to referenced artifact bytes. |
| Raw `bytes` with `from_object` | Unsupported directly; use byte hashing plus `from_cid`, a file, or a serializer hook. |
| List input/output mismatch | Choose whole-list asset versus per-element assets explicitly. |
| `from_cid()` | Registers a typed reference; does not fetch/store the preimage. |
| Standalone document export | Supported by current retrieval; no invented packaging computation needed. |
| Multiple signers in one process | Setter supports switching; global state needs coordination. |
| Wrapper or additional code asset | Allowed; describe captured scope accurately. |
| Green verifier | Cryptographic checks passed within their scope, not proof of execution correctness. |
| `@compute` inside `graph_context` | Pass `ctx=` explicitly, or the computation lands in the default context. |
| `Context.from_uuid` argument | Standard-library `uuid.UUID`, not `sdk.UUID` or `str`. |
| Setup order | `init()` → signer → `set_active_signer()` → register. Every statement needs an active signer. |
| Tuple/bytes/set as `@compute` input | `TypeError` before the function runs; use a list or an asset. |
| Sync generator with `@compute` | Unsupported; fails with `TypeError`. |
| `_store=False` and metadata | Metadata JSON is always stored and exported. |
| Rewriting functions to take/return named assets | Not required by the SDK. Edits must preserve behavior and record only real data flow; no naming helpers (§6.10). |

Sources and qualifications for these entries appear in §2–§11.

## 13. API and upstream references

### 13.1 Installation and instrumentation policy

Install the public package with `python -m pip install eqty_sdk`; import it as
`eqty_sdk`. Upstream's project metadata requires Python 3.10 or newer. Use a tested,
compatible release in your application's dependency file. This audit does not change
any project's installed version or dependency pin. [Sources: package metadata][package],
[installation guidance][quick-start-docs].

The SDK does not prohibit decorator wrappers, optional integrations, or instrumentation
modules. **Recommendation:** if evidence is required for a workflow, make failures
visible rather than silently replacing SDK calls with no-ops. Keep wrappers small
and test the resulting statements. Whether instrumentation is mandatory is a product
policy, not a universal SDK constraint.

### 13.2 Official examples

Use the [example directory][examples] for supported patterns. The reviewed snapshot
contains these examples:

| Example | Demonstrates |
|---|---|
| `quick-start.py` | Setup with a SECP256R1 signer and `DID.from_signer`, all three asset constructors, builder, decorator, export. |
| `basic-workflow.py` | Ordinary arguments, plain return value, `output_type`. |
| `async-compute.py` | Async function and async generator. |
| `path-backed-assets.py` | File-backed typed assets. |
| `custom-serialize.py` | Spark input adapter with `to_eqty_asset()`. |
| `context-linking.py` | Parent/child contexts and explicit context factories. |
| `creating-the-model.py` | Model creation and an existing project UUID. |
| `using-the-model.py` | Referencing a model by CID. |
| `model-signing.py` | Optional model-directory signing with a SECP256R1 signer. |
| `service-registering.py` | Registering a context with a service. |
| `auth-service-signer.py` | Service-backed signing. |
| `association-and-compute.py` | Associations with CID, UUID, and DID references. |

Examples may need data, optional packages, or services. Consult their actual source
and the implementation when behavior is unclear; an example is not a complete
security or compatibility specification.

### 13.3 Offline verification

The changelog introduces these APIs in **2.4.0**, not 2.4.1:

```python
# Both accept JSON text, not a Python statement/credential dictionary.
sdk.verify_statement(statement_json, contexts=None)
sdk.verify_vc(vc_json, statement_id=None, contexts=None)
```

- `verify_statement()` recomputes the statement's canonicalized RDF CID after
  removing `@id`. `True` covers fields defined by the JSON-LD context; undefined
  JSON keys may be dropped during expansion. It is not byte-for-byte JSON validation.
- `verify_vc()` checks a W3C credential's proof and, when supplied, its expected
  `credentialSubject.id`. Pass the **credential**, not its enclosing
  `CredentialRegistration` statement. Do not pass every statement to both APIs.
- Verification is offline. Supported offline DID methods are `did:key`, `did:jwk`,
  and `did:pkh`; network-dependent DID methods raise. Revocation/suspension status
  is not checked.
- Supply the manifest's JSON-LD `contexts` when required. Supplied context documents
  take precedence over embedded documents with the same URI. Treat context provenance
  and any required vocabulary allow-list as part of the verifier's trust policy.
- Malformed input can raise `ValueError`. Unresolved statement contexts raise;
  unresolved credential contexts can return `False`, like a bad proof.
- These APIs do not independently verify all asset blobs, authorize issuers,
  prove completeness, or verify every alternative credential format. There is no
  single manifest-level verification entry point.

[Sources: verifier implementation][verification], [verification documentation][verification-docs],
[changelog][changelog].

### 13.4 Public APIs this guide does not cover

The package also exports `Association` / `ASSOCIATION_TYPES` (a builder for
`Certifies`, `Includes`, and `IsInstanceOf` links between CIDs, UUIDs, and DIDs),
`Declaration` (governance documents attached with `asset.add_declaration(...)`),
`DID` (for example `DID.from_signer(signer, name=...)`), `Entity` (UUID-identified
things without content), and `Service` (for `Context.register`). Advanced statement
functions live in `eqty_sdk._rust.statements`, and upstream recommends the
higher-level APIs where they fit. See the [upstream API docs][api-docs] and the
association example.

## 14. Audit record: claims removed or narrowed

This revision replaces application anecdotes as SDK authority. Major corrections:

| Previous claim | Upstream-backed correction |
|---|---|
| One identity per process, forever | Active signer can be changed; concurrent coordination is still necessary. |
| Reuse requires `_load_if_exists=True` | Prefer `load_or_create`; the legacy flag is deprecated. |
| Missing names / bare `Custom` are defects | Both are explicitly supported and tested. |
| Always extract shared naming functions | Optional convention; inline constructors are official usage. |
| Every instrumented function must return a named asset | Plain values and `output_type` are supported. |
| Source inspection occurs at decoration | `@compute` constructs `Compute` and inspects source on invocation. |
| `from_cid` does not re-register | It registers data and metadata statements by default. |
| Standalone assets cannot export | Fixed upstream in 2.4.0; directly linked statements are retrieved. |
| Every object hashes as JSON | Serialization is type-dependent; strings/numbers/paths differ. |
| A raw `Path` commits to file content | General object serialization hashes the path text. |
| All builder references accept lists | Input/output methods do; computation setters have distinct single-reference semantics. |
| Bytes must be written to disk | Direct byte hashing plus typed CID registration is supported. |
| Identifier hashing / selection necessarily creates cycles | Relationship-dependent modeling issue, not an inherent SDK failure. |
| `_store=True` directly embeds; `_store=False` guarantees privacy | Local retention and later export are separate; directory structure and existing blobs matter. |
| `from_path` captures one immutable snapshot | File hashing and storage can read separately. |
| Import validates participant evidence | Import stores/links statements; cryptographic and policy checks are separate. |
| Verification was new in 2.4.1 | The official changelog records it in 2.4.0. |
| Original functions only; no wrappers or extra modules | Project instrumentation policy, not an SDK restriction. |
| Nested manifests crash Explorer universally | Not established by the SDK repository; removed as a general rule. |
| `Context.from_uuid(sdk.UUID(text))` | Requires a stdlib `uuid.UUID`; `sdk.UUID` raises `TypeError`. |
| "Prefer" explicit `ctx=` for decorators | Required: without it, `@compute` splits assets and computation across contexts under `graph_context`. |
| Tuples use the general serializer as input | They raise `TypeError`, as do `bytes`, `set`, and sync generators. |
| (unstated) setup prerequisites | `init()` before signers; an active signer before any registration; `init()` is once per process. |
| (unstated) metadata privacy | Metadata blobs are stored and exported regardless of `_store`. |
| (unstated) config persistence | `set_store_all_blobs` and ignore rules persist in `config.toml`; omitted ignore-rule arguments reset to defaults. |
| Explicit operators are generally available | Only through the advanced statement API or a VComp notary; builder/decorator use the signer. |

Former FLARE-specific node counts, filename bans, tensor encodings, and UI anecdotes
are not retained as SDK guarantees. Other local skills may still impose their own
project policies; those policies should not be cited as upstream API requirements.

[upstream]: https://github.com/eqtylab/integrity-py/tree/58dd0a21bb6276e44db42c2ce0cc225417fc466a
[changelog]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/CHANGELOG.md
[public-api]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/eqty_sdk/__init__.py
[assets]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/eqty_sdk/asset/asset.py
[asset-docs]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/docs/api/assets.md
[name-tests]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/tests/asset_tests/test_asset_names.py
[type-tests]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/tests/asset_tests/test_asset_types.py
[metadata]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/eqty_sdk/metadata.py
[signers]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/src/signer.rs
[signer-tests]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/tests/signer_tests/test_signer.py
[config]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/src/config.rs
[config-docs]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/docs/api/init-and-config.md
[contexts]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/src/indexer/mod.rs
[indexer]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/src/indexer/sqlite.rs
[graph-context]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/eqty_sdk/context.py
[builder]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/eqty_sdk/compute/computation.py
[computation-statements]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/src/statements/computation.rs
[metadata-statements]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/src/statements/metadata.rs
[api-docs]: https://github.com/eqtylab/integrity-py/tree/58dd0a21bb6276e44db42c2ce0cc225417fc466a/docs/api
[compute]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/eqty_sdk/compute/compute.py
[decorator]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/eqty_sdk/compute/decorator.py
[compute-tests]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/tests/computation_tests/test_compute_decorator.py
[none-tests]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/tests/computation_tests/test_computation_none_return.py
[hashing]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/src/lib.rs
[cid-tests]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/tests/core_tests/test_cid.py
[verification]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/src/verification.rs
[verification-docs]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/docs/api/verification.md
[package]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/pyproject.toml
[quick-start-docs]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/docs/quick-start.md
[examples]: https://github.com/eqtylab/integrity-py/tree/58dd0a21bb6276e44db42c2ce0cc225417fc466a/examples
[quick-start]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/examples/quick-start.py
[basic-example]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/examples/basic-workflow.py
[context-example]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/examples/context-linking.py
[async-example]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/examples/async-compute.py
[association-example]: https://github.com/eqtylab/integrity-py/blob/58dd0a21bb6276e44db42c2ce0cc225417fc466a/examples/association-and-compute.py
