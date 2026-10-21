# Question → nodes

Each recipe is an `explorer.py ids` selector plus the command that shows the
result. `E` below stands for `python3 <skill-dir>/explorer.py`. Get node ids
from `E nodes`: its lines are `id  type  name`.

| Question | Nodes | Show with |
|---|---|---|
| What was this model / output made from? | `E ids --upstream <id>` | `only` |
| What used this dataset? What came out of it? | `E ids --downstream <id>` | `only` |
| Everything connected to this step | `E ids --lineage <id>` | `only` |
| How did A lead to B? | `E ids --between <A> <B>` | `only`; if empty, say there is no path |
| Which steps ran on this machine? | `E ids --on <did> --with-io` | `only`, then `trust <did>` |
| Which steps ran on these machines? (several DIDs) | `E ids --on <did1> --with-io` plus the same for each DID | one `only` with all the ids; `trust` on one, offer the rest |
| What did this signer register? | `E ids --signer <did>` | `only` |
| Show me the computations | `E ids --computations --with-io` | `only` |
| What's missing? | `E ids --missing` | `only --note "bytes not in the manifest"` |
| What is this node? (after a click) | `E selection` | answer about it; `select` keeps it open |

## Questions `ids` can't select: `query`

The explorer has its own query language (Lucene-style). Use it for fields
the selectors above don't cover. The command refuses unknown fields; values
it can't check, so read `shown` in the reply.

| Question | Command |
|---|---|
| Which models (datasets, …) are there? | `E query 'assetType:Model'` |
| Models or datasets | `E query 'assetType:Model OR assetType:Dataset'` |
| What did this team register? (by name) | `E query 'registeredBy:"ml-team"'` |
| What is owned by …? (metadata) | `E query 'metadata.owner:ml*'` |
| Numbers in metadata | `E query 'metadata.epochs:>=3'`, `metadata.size:[100 TO 500]` |
| What was registered after / before a date? | `E query 'after:2025-10-01'`, `before:2025-12` |
| Nodes with no credential | `E query '-has:vc'` |
| Computations only | `E query 'is:computation'` |
| Text anywhere | `E query 'qwen'` (bare text) |

- **Fields:** `name`, `assetType` (`type`), `id`, `cid`, `is` (`data` /
  `computation`), `registeredBy` (`by`), `operatedBy`, `executedOn`,
  `statement`, `has`, `metadata.<key>` (`meta.<key>`), `associationType`,
  `timestamp`, `after`, `before`, `upstream`, `downstream`, `lineage`.
- **Syntax:** adjacent terms mean AND. Precedence is NOT (`-` or `NOT`), then
  AND, then OR; parentheses override it. A bare value ignores case and takes
  `*` and `?`; a quoted value matches exactly. **Quote any value with a `:`
  in it**, such as a DID: `executedOn:"did:key:z…"`.
- **What it can't do:** dim, or tell you which nodes matched (only how many).
  After a `query`, `hide` and `show <ids>` refuse; use `only`, `show` or
  `reset`.
- **When `ids` can answer, use `ids`.** You then know the exact set, and the
  answer comes from eqty-manifest, not from the explorer's matching.

## Hardware and trust questions

"Did this run on attested hardware?" and "can I trust this machine?" follow
this order:

1. **Quote eqty-manifest's summary block**, as that skill says.
2. **Find the machine's DID** in the block's *Execution environment* lines, or
   in `parse_manifest.py <manifest> attestation`. "The TDX machine" means every
   DID with a TDX line; there may be more than one.
3. **Show its steps:** `E only $(E ids --on <did> --with-io) --note
   "<what the block says, e.g. report signature verified · key binding verified>"`.
4. **Open its chain of trust:** `E trust <did>`. If the reply says
   `"found": false`, the chain-of-trust app has no node for that DID. Say so.

**Several machines with the same verdict:** one `only` with all their steps,
and a note naming each machine with its verdict. Open `trust` on one of them,
say which, and offer the others.

**If some machines verified and others didn't,** show one group per answer:
the group the user asked about, with the verdict in the note, then offer the
other group as a next step. Never show one group and replace it with the next
in the same turn; the user sees only the last. Never let colour stand in for
the verdict, and never let a `has:vc` match stand in for the block.

## Order of operations

- `only` and `query` replace the previous view. There's no need to `reset`
  between answers on the same subject.
- `hide` and `show <ids>` adjust what `only` (or `show`) put on the canvas.
- `select` on a filtered-out node clears the query first, so the whole graph
  comes back with that node selected.
- A query the user types replaces yours. `status` shows it; ask before
  replacing it if they seem to be using it.
