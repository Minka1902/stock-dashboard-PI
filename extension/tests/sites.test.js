import { test } from "node:test";
import assert from "node:assert/strict";

import { siteKey, isSiteEnabled, CONTENT_HOSTS } from "../src/lib/sites.js";

test("www and subdomains collapse onto the canonical key", () => {
  assert.equal(siteKey("www.reddit.com"), "reddit.com");
  assert.equal(siteKey("old.reddit.com"), "reddit.com");
  assert.equal(siteKey("reddit.com"), "reddit.com");
  assert.equal(siteKey("www.cnbc.com"), "cnbc.com");
});

test("finance.yahoo.com is its own key, not yahoo.com", () => {
  assert.equal(siteKey("finance.yahoo.com"), "finance.yahoo.com");
});

test("unlisted hosts fall back to their own hostname", () => {
  assert.equal(siteKey("example.com"), "example.com");
  assert.equal(siteKey("www.example.com"), "example.com");
});

test("siteKey is case-insensitive and null-safe", () => {
  assert.equal(siteKey("WWW.Reddit.COM"), "reddit.com");
  assert.equal(siteKey(null), "");
});

test("a host is enabled unless explicitly switched off", () => {
  const s = { overlayEnabled: true, sites: {} };
  assert.equal(isSiteEnabled(s, "www.reddit.com"), true);
});

test("switching off the canonical key disables its subdomains too", () => {
  const s = { overlayEnabled: true, sites: { "reddit.com": false } };
  assert.equal(isSiteEnabled(s, "old.reddit.com"), false);
  assert.equal(isSiteEnabled(s, "www.reddit.com"), false);
  assert.equal(isSiteEnabled(s, "x.com"), true);
});

test("the global switch overrides per-site settings", () => {
  const s = { overlayEnabled: false, sites: { "x.com": true } };
  assert.equal(isSiteEnabled(s, "x.com"), false);
});

test("every declared content host is its own canonical key", () => {
  // Guards against adding a host to the list in a form that siteKey rewrites,
  // which would make its options checkbox control the wrong key.
  for (const h of CONTENT_HOSTS) assert.equal(siteKey(h), h);
});
