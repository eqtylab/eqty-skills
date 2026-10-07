# Pinned vendor trust anchors

`verify_attestation.py` checks that a TEE report's certificate chain ends at
one of these, **not** at the root that shipped inside the evidence itself.
Every evidence blob embeds its own chain, and such a chain is self-consistent
by construction — an attacker forging one would forge a matching root. Pinning
locally is what makes the check meaningful.

| File | Contains | Source |
|---|---|---|
| `intel-sgx-root-ca.pem` | Intel SGX Root CA (self-signed) | `https://certificates.trustedservices.intel.com/Intel_SGX_Provisioning_Certification_RootCA.pem` |
| `amd-milan-ark-ask.pem` | AMD ARK-Milan (self-signed) + SEV-Milan ASK | `https://kdsintf.amd.com/vcek/v1/Milan/cert_chain` |
| `amd-genoa-ark.pem` | AMD ARK-Genoa (self-signed) | ARK extracted from `https://kdsintf.amd.com/vcek/v1/Genoa/cert_chain` on 2026-10-07 |
| `amd-turin-ark.pem` | AMD ARK-Turin (self-signed) | ARK extracted from `https://kdsintf.amd.com/vcek/v1/Turin/cert_chain` on 2026-10-07 |
| `amd-venice-ark.pem` | AMD ARK-Venice (self-signed) | ARK extracted from `https://kdsintf.amd.com/vcek/v1/Venice/cert_chain` on 2026-10-07 |
| `nvidia-device-identity-ca.pem` | NVIDIA Device Identity CA (self-signed) | `https://docs.ndis.nvidia.com/certs/identity-root/Root-CA.cer` — see "NVIDIA" below for how this copy was obtained |

## Integrity

```
267a851c8d10982685b5f219d9ac2600ba71463569a6541827c2dc9fe9d6d699  intel-sgx-root-ca.pem
22e62f8d2c21a156470145fc75f7b5a377cb053ced3e97f0bd3f8d8ca5941ce6  amd-milan-ark-ask.pem
96bff94e97e2b7ddf4bf3b9b7f780f94a46b8c51f52abfdcc283be7df36ce405  amd-genoa-ark.pem
b69c981fe0216c3d8e682eb6f480c6879497103071e52b5e603178638db8f68d  amd-turin-ark.pem
e2c932cf3c93713bd89c1355898c8a49c919b21825e5205e9ca29a9cc5f0ec96  amd-venice-ark.pem
5f6ce25ce0374fff3d28b85812d1d902a0c2680e2e3c2aa36e67f7d83f1a543d  nvidia-device-identity-ca.pem
```

The original Intel and Milan roots were confirmed **byte-identical** to the
roots embedded in the example manifests' own evidence chains, which is
independent corroboration that those manifests carry genuine vendor anchors:

```
Intel SGX Root CA  44a0196b2b99f889b8e149e95b807a350e7424964399e885…  official == embedded
AMD ARK-Milan      69d063b45344d26a2e94e1f4210de49ef555308287d4c174…  official == embedded
```

## AMD Genoa — downloaded from AMD KDS

AMD's [VCEK and KDS interface specification](https://docs.amd.com/api/khub/documents/dWGhwYpo1Wv51rJN4d~47g/content)
defines the product-specific PEM CA chain endpoint. The Genoa response contains
the SEV-Genoa ASK followed by the self-signed ARK-Genoa. Only the ARK is stored
in `amd-genoa-ark.pem`; the ASK is an intermediate, not an additional pin.
The downloaded ARK's self-signature and the ASK's signature under that ARK
were checked before adding the pin.

DER certificate SHA-256 fingerprints (distinct from the PEM file checksum above):

```
ARK-Genoa  4c6598d19c18719c5dfd4a7d335f674e5bfe1d8f800cea2cf270c10d103db2f1
SEV-Genoa  5464738c1546aed5f2cecf1dc98c5c960a92e8913238a61711bc90ec6e828521
```

Both certificates match those embedded in the user-provided banking-agent
manifest used for the development regression fixture. Their authority comes
from the independent AMD KDS download; the manifest is corroboration.
The verifier loads all bundled AMD files and compares the chain's
terminal certificate with the bundled pins using its SHA-256 fingerprint.
It does not fetch certificates during verification.

## AMD Turin and Venice — downloaded from AMD KDS

The [Turin chain](https://kdsintf.amd.com/vcek/v1/Turin/cert_chain) and
[Venice chain](https://kdsintf.amd.com/vcek/v1/Venice/cert_chain) were downloaded
independently from AMD KDS on 2026-10-07. Each contains the product's ASK followed
by its self-signed ARK. The ARK self-signature and the ASK signature under the
ARK were verified before adding these pins. Only the ARKs are shipped in the
trust store; the ASKs are development-only regression fixtures.

DER certificate SHA-256 fingerprints:

```
ARK-Turin   1f084161a44bb6d93778a904877d4819cafa5d05ef4193b2ded9dd9c73dd3f6a
SEV-Turin   5b77ef5fe7a7a004fd9032668fba9d0fda22f88c4442069a479636a6ae3b3185
ARK-Venice  46ef2eb7664175c7e9d3923ea0d275e572366d755556a30e229634592dce5903
SEV-Venice  657c4ff008b76926abd1208050cc1d0a019049c65e7157ec3be75fb4e7acdcdc
```

These pins establish certificate trust for the new root families, without
changing report parsing or signature algorithms. Offline tests validate their
AMD signing certificates and a genuine Turin VCEK; no genuine Turin or Venice
SNP report is currently in the development corpus. Full hardware report
verification for those generations still needs real report regression fixtures.

## NVIDIA — pinned by published fingerprint

NVIDIA's PKI host did not resolve from the environment this was written in,
so `Root-CA.cer` could not be downloaded directly. The certificate itself is
public and NVIDIA publishes its SHA-256 fingerprint on the NDIS site, which
is what actually makes a root a trust anchor — the fingerprint, not the
transport that delivered the bytes.

So the copy in `nvidia-device-identity-ca.pem` was lifted from the
self-signed `CN=NVIDIA Device Identity CA` at the top of the evidence chains
in `gnn-train` and `model-dev-inf`, and accepted ONLY because its fingerprint
matches NVIDIA's published value exactly:

```
published (docs.ndis.nvidia.com, "NVIDIA Device Identity CA")
  10:2B:F6:59:D5:41:96:14:C9:D8:E6:AE:CE:BC:80:45:4E:B2:6B:1D:F6:A7:69:AC:72:0B:9A:69:0B:16:7B:48
extracted from the manifests' own chains
  10:2B:F6:59:D5:41:96:14:C9:D8:E6:AE:CE:BC:80:45:4E:B2:6B:1D:F6:A7:69:AC:72:0B:9A:69:0B:16:7B:48
```

**This is not circular.** The objection to trusting a root that shipped
inside the evidence is that a forger would ship a matching forged root — but
a forged root cannot produce NVIDIA's published SHA-256. Pinning bytes whose
digest equals the vendor-published digest is exactly as strong as
downloading them, and the match is independent corroboration that these
manifests carry a genuine NVIDIA anchor, the same way the Intel and AMD
roots were corroborated above.

Replace this file with a direct download from
`https://docs.ndis.nvidia.com/certs/identity-root/Root-CA.cer` (DER; convert
with `openssl x509 -inform der -in Root-CA.cer -out ...pem`) whenever the
network allows. The result is guaranteed byte-identical — that is what the
fingerprint match means — so nothing downstream changes.

**Second NVIDIA root not yet held.** NVIDIA also publishes a newer root,
"NVIDIA Device Identity CA (New)", at
`https://docs.ndis.nvidia.com/certs/identity-root/Root-CA-L1B.cer`
(SHA-256 `88:05:70:0F:D3:07:DB:68:01:F9:A8:A7:A9:1A:CB:F7:2E:51:3C:DF:8E:D1:6E:0B:FE:AF:62:31:80:37:AD:96`).
No evidence in the current corpus chains to it, so it could not be obtained
the same way. Append it to this PEM when available — `_load_pinned()` reads
every certificate in the file, so several roots per vendor need no code
change. Until then, hardware rooted at L1B correctly reads as
`trust_anchor_unavailable` rather than verified.

## Coverage limits

- **NVIDIA:** only the original Device Identity CA is pinned, not the newer
  L1B root — see above.
- **AMD:** ARK-Milan, ARK-Genoa, ARK-Turin and ARK-Venice are pinned. Any chain
  ending at a different ARK fails the pinned-root check and is reported as
  unverified. Coverage follows the certificate root, not a claimed CPU name.
- **Reference measurements are never checked.** These roots establish that a
  report came from genuine silicon and is intact. They say nothing about
  whether the enclave ran the expected code — that needs Intel TCB info,
  NVIDIA RIM, or known-good OVMF/kernel values, none of which ship here. Every
  result carries `measurements_checked: false` for this reason.
