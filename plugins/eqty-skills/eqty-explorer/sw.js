// Answers the explorer's fetch of a pinned JSON-LD context from the copy explorer.py
// fills in here, so the request never reaches the network. Every other request passes
// through untouched. The page's CSP allows only the pinned URLs on that host.
"use strict";
const PINNED = __CONTEXTS__;
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  const doc = PINNED[event.request.url];
  if (doc) {
    event.respondWith(new Response(JSON.stringify(doc), { headers: { "Content-Type": "application/ld+json" } }));
  }
});
