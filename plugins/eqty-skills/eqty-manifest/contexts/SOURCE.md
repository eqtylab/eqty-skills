# JSON-LD contexts (pinned copies)

EQTY's identity, component and state attestation credentials name these
contexts. Manifests do not embed them and `eqty_sdk` does not compile them in,
so `verify_credentials.py` supplies these copies to the SDK. Each is used only
after its hash matches the one below; a context the manifest embeds for the
same URL takes precedence.

| File | URL | Fetched | Last-Modified |
|---|---|---|---|
| `identity-attestation-v1.json` | `https://ns.eqtylab.io/vc/identity-attestation/v1` | 2026-10-19 | Mon, 19 Oct 2026 15:57:16 GMT |
| `component-attestation-v1.json` | `https://ns.eqtylab.io/vc/component-attestation/v1` | 2026-10-23 | Mon, 19 Oct 2026 19:54:14 GMT |
| `state-attestation-v1.json` | `https://ns.eqtylab.io/vc/state-attestation/v1` | 2026-10-23 | Fri, 23 Oct 2026 16:20:56 GMT |

`identity-attestation-v1.json` is the same copy as `eqty-explorer/contexts/`.
None of them imports another context. Refresh:
`curl -sfL -H "Accept: application/ld+json" <URL> -o <file>`, then update the
hash below.

```
b43e09a46daf28b5c5abace0febde3a34a75a3a15c5237725f2420ba80468c18  identity-attestation-v1.json
5e6568c6bf5c4140bef783f4e5d27ac8a4b06a9ad166b1cf6b9c02d54230ba06  component-attestation-v1.json
1ec90d4cda1944ab99c73c37644fd8afc5de425ed4181d40c4bb362e246c7729  state-attestation-v1.json
```
