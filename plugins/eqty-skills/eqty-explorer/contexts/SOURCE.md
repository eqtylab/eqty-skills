# JSON-LD contexts (pinned copies)

The explorer fetches `https://ns.eqtylab.io/vc/identity-attestation/v1` to verify
identity-attestation credentials. The page's service worker (`sw.js`) answers
that exact URL with this copy, so the request never leaves the machine.

| File | URL | Fetched | Last-Modified |
|---|---|---|---|
| `identity-attestation-v1.json` | `https://ns.eqtylab.io/vc/identity-attestation/v1` | 2026-10-19 | Mon, 19 Oct 2026 15:57:16 GMT |

Refresh: `curl -sfL -H "Accept: application/ld+json" <URL> -o <file>`, then update
the hash below (the tests check it).

```
b43e09a46daf28b5c5abace0febde3a34a75a3a15c5237725f2420ba80468c18  identity-attestation-v1.json
```
