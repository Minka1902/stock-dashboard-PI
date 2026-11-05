import { test } from "node:test";
import assert from "node:assert/strict";

import { buildMatcher, scanText, STOPWORDS } from "../src/content/detect.js";

const known = buildMatcher(["AAPL", "TSLA", "NVDA", "BRK.B", "ALL", "IT", "ON", "DD", "AI", "F"]);
const cashtagOnly = buildMatcher(null);

const tickers = (text, m = known) => scanText(text, m).map((x) => x.ticker);

test("cashtags always match and normalize to uppercase", () => {
  assert.deepEqual(tickers("look at $nvda today"), ["NVDA"]);
  assert.deepEqual(tickers("$AAPL and $TSLA"), ["AAPL", "TSLA"]);
});

test("cashtags match even for symbols the backend doesn't know", () => {
  assert.deepEqual(tickers("$ZZZZ is obscure"), ["ZZZZ"]);
});

test("class-share cashtags keep their suffix", () => {
  assert.deepEqual(tickers("$BRK.B held up"), ["BRK.B"]);
});

test("bare symbols match only when known", () => {
  assert.deepEqual(tickers("TSLA beat estimates"), ["TSLA"]);
  assert.deepEqual(tickers("ZZZZ beat estimates"), []);
});

test("stopwords are never matched bare, even when they are real tickers", () => {
  // ALL, IT, ON, DD and AI are in the known set above and are still suppressed.
  assert.deepEqual(tickers("ALL IN ON IT is my DD on AI"), []);
});

test("a stopword is still matched as a cashtag", () => {
  assert.deepEqual(tickers("$ALL is an insurer"), ["ALL"]);
});

test("every stopword is suppressed in bare form", () => {
  const all = buildMatcher([...STOPWORDS]);
  for (const w of STOPWORDS) {
    assert.deepEqual(tickers(`the ${w} thing`, all), [], `expected ${w} to be suppressed`);
  }
});

test("lowercase prose is never matched bare", () => {
  assert.deepEqual(tickers("tsla and aapl and nvda"), []);
});

test("single letters are not matched bare", () => {
  // "F" is a real ticker and is in the known set, but a bare "F" in prose is noise.
  assert.deepEqual(tickers("grade F work"), []);
  assert.deepEqual(tickers("$F is Ford"), ["F"]);
});

test("punctuation around a symbol does not break the match", () => {
  assert.deepEqual(tickers("(TSLA) rallied"), ["TSLA"]);
  assert.deepEqual(tickers("TSLA's guidance"), ["TSLA"]);
  assert.deepEqual(tickers("AAPL, NVDA and TSLA"), ["AAPL", "NVDA", "TSLA"]);
});

test("cashtag-only mode ignores bare symbols", () => {
  assert.deepEqual(tickers("TSLA and $AAPL", cashtagOnly), ["AAPL"]);
});

test("a cashtag is not also reported as a bare match", () => {
  const hits = scanText("$TSLA", known);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].cashtag, true);
});

test("offsets point at the matched span", () => {
  const text = "buy $TSLA now";
  const [hit] = scanText(text, known);
  assert.equal(text.slice(hit.start, hit.end), "$TSLA");
});

test("results come back in document order", () => {
  const hits = scanText("NVDA then $AAPL then TSLA", known);
  assert.deepEqual(
    hits.map((h) => h.start),
    [...hits.map((h) => h.start)].sort((a, b) => a - b),
  );
});

test("empty and trivial input is handled", () => {
  assert.deepEqual(tickers(""), []);
  assert.deepEqual(tickers("a"), []);
  assert.deepEqual(scanText(null, known), []);
});
