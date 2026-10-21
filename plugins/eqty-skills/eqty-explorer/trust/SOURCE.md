# Chain-of-trust app (pinned copy)

A copy of the build deployed at https://vcomp-poc-chain-of-trust.vercel.app/, fetched 2026-10-13
(its Last-Modified: Tue, 13 Oct 2026 15:24:02 GMT). explorer.py serves it on its own local origin,
under its own CSP, so it runs as fixed code with no network access.

Refresh: fetch `/` and the three files `index.html` names under `/assets/`
(the JS names the wasm), replace these, and update the hashes below.

```
8422e58b7a6fe2b8c9cff74b026f9f36229cebeea1b5286c55f1ec5b9b254955  index.html
15efb0c196c298210ea524e0018f7fd115fcb5eb8ea20ca81b5a9ca2113e42a9  assets/index-BPnLoUUi.css
1329b9879a0c8df501704496cafc0aeaa5b25b5f9306ceb8598634407ce537e8  assets/index-CgK1dtQp.js
dbb6ea24acf97f48dde3b916bd97f635ab6aebe2e0ec1a247eca5f28bf8f0ff2  assets/vcomp_verify_wasm_bg-XEsSfHDz.wasm
```
