import { test } from "node:test";
import assert from "node:assert/strict";

import { normalizeBase, originPattern, stockUrl } from "../src/lib/storage.js";

test("normalizeBase strips paths and trailing slashes", () => {
  assert.equal(normalizeBase("http://localhost:8000/"), "http://localhost:8000");
  assert.equal(normalizeBase("http://localhost:8000/dashboard"), "http://localhost:8000");
  assert.equal(normalizeBase("  http://localhost:8000  "), "http://localhost:8000");
});

test("normalizeBase assumes http when no scheme is given", () => {
  assert.equal(normalizeBase("localhost:8000"), "http://localhost:8000");
});

test("normalizeBase keeps https and non-default ports", () => {
  assert.equal(normalizeBase("https://stocks.example.com"), "https://stocks.example.com");
  assert.equal(normalizeBase("https://stocks.example.com:8443"), "https://stocks.example.com:8443");
});

test("normalizeBase rejects non-http schemes", () => {
  // This is the guard that keeps javascript: out of tabs.create and fetch.
  assert.equal(normalizeBase("javascript:alert(1)"), null);
  assert.equal(normalizeBase("file:///etc/passwd"), null);
  assert.equal(normalizeBase("chrome-extension://abc"), null);
});

test("normalizeBase rejects empty input", () => {
  assert.equal(normalizeBase(""), null);
  assert.equal(normalizeBase("   "), null);
  assert.equal(normalizeBase(null), null);
  assert.equal(normalizeBase(undefined), null);
});

test("originPattern builds a host permission match", () => {
  assert.equal(originPattern("http://localhost:8000"), "http://localhost:8000/*");
});

test("stockUrl builds the dashboard's deep-link route", () => {
  // Route confirmed in frontend/src/lib/nav.js:3,17 — #/stock/<TICKER>.
  assert.equal(stockUrl("http://localhost:8000", "NVDA"), "http://localhost:8000/#/stock/NVDA");
});

test("stockUrl encodes symbols with a class suffix", () => {
  assert.equal(stockUrl("http://x.test", "BRK.B"), "http://x.test/#/stock/BRK.B");
});
