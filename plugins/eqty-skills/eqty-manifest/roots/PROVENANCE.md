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
| `nvidia-device-identity-ca.pem` | NVIDIA Device Identity CA (self-signed) | `https://docs.ndis.nvidia.com/certs/identity-root/Root-CA.cer` — see "NVIDIA" below for how this copy was obtained |

## Integrity

```
267a851c8d10982685b5f219d9ac2600ba71463569a6541827c2dc9fe9d6d699  intel-sgx-root-ca.pem
22e62f8d2c21a156470145fc75f7b5a377cb053ced3e97f0bd3f8d8ca5941ce6  amd-milan-ark-ask.pem
5f6ce25ce0374fff3d28b85812d1d902a0c2680e2e3c2aa36e67f7d83f1a543d  nvidia-device-identity-ca.pem
```

Both were confirmed **byte-identical** to the roots embedded in the example
manifests' own evidence chains, which is independent corroboration that those
manifests carry genuine vendor anchors:

```
Intel SGX Root CA  44a0196b2b99f889b8e149e95b807a350e7424964399e885…  official == embedded
AMD ARK-Milan      69d063b45344d26a2e94e1f4210de49ef555308287d4c174…  official == embedded
```

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
- **AMD:** only the Milan product line is pinned. Genoa, Bergamo, Turin, etc.
  each have their own ARK; a report from one of those will fail the pinned-root
  check and must be reported as unverified rather than assumed good.
- **Reference measurements are never checked.** These roots establish that a
  report came from genuine silicon and is intact. They say nothing about
  whether the enclave ran the expected code — that needs Intel TCB info,
  NVIDIA RIM, or known-good OVMF/kernel values, none of which ship here. Every
  result carries `measurements_checked: false` for this reason.
