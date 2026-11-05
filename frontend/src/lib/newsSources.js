/**
 * One "source" vocabulary across both feeds.
 *
 * GDELT articles carry a `domain` (reuters.com); X posts carry an `account`
 * handle. Each X account is a source in its own right — @realDonaldTrump and
 * @aistocksavvy are no more the same source than reuters.com and ft.com are.
 * Normalising here lets the two feeds be filtered and sorted as one list.
 */

/** Strip a leading www. so "www.reuters.com" and "reuters.com" don't split. */
function cleanDomain(domain) {
  return String(domain || "").replace(/^www\./i, "").toLowerCase();
}

/** Tickers an item is tagged with (X posts can carry several). */
function tickersOf(item, kind) {
  if (kind === "x") {
    return String(item.tickers || "").split(",").map((t) => t.trim()).filter(Boolean);
  }
  return item.ticker ? [item.ticker] : [];
}

/**
 * Merge articles and X posts into one sortable list.
 * `when` is epoch ms so sorting is numeric and stable.
 */
export function unifyFeed(news = [], xPosts = []) {
  const items = [];

  for (const a of news) {
    items.push({
      id: `news:${a.url}`,
      kind: "news",
      source: cleanDomain(a.domain) || "unknown",
      when: Date.parse(a.seendate) || 0,
      tickers: tickersOf(a, "news"),
      raw: a,
    });
  }

  for (const p of xPosts) {
    items.push({
      id: `x:${p.account}:${p.post_id}`,
      kind: "x",
      source: `@${p.account}`,
      when: Date.parse(p.posted_at) || 0,
      tickers: tickersOf(p, "x"),
      raw: p,
    });
  }

  return items;
}

/** Every source present, with its item count — most prolific first. */
export function sourceCounts(items) {
  const counts = new Map();
  for (const it of items) {
    const cur = counts.get(it.source) || { source: it.source, kind: it.kind, count: 0 };
    cur.count += 1;
    counts.set(it.source, cur);
  }
  return [...counts.values()].sort(
    (a, b) => b.count - a.count || a.source.localeCompare(b.source),
  );
}

/** Sort accessors for useSortableRows. Module-scope so the memo stays stable. */
export const SORT_COLUMNS = {
  when: (it) => it.when,
  source: (it) => it.source,
};
