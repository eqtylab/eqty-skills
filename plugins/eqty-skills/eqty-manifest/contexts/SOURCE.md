# JSON-LD contexts (pinned copies)

EQTY's identity-attestation credentials name this context. Manifests do not
embed it and `eqty_sdk` does not compile it in, so `verify_credentials.py`
supplies this copy to the SDK. It is used only after its hash matches the one
below; a context the manifest embeds for the same URL takes precedence.

| File | URL | Fetched | Last-Modified |
|---|---|---|---|
| `identity-attestation-v1.json` | `https://ns.eqtylab.io/vc/identity-attestation/v1` | 2026-10-19 | Mon, 19 Oct 2026 15:57:16 GMT |

The same copy as `eqty-explorer/contexts/`. Refresh: `curl -sfL -H "Accept: application/ld+json" <URL> -o <file>`,
then update the hash below.

```
b43e09a46daf28b5c5abace0febde3a34a75a3a15c5237725f2420ba80468c18  identity-attestation-v1.json
```
