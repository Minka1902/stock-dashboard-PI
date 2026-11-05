import { useMemo, useState } from "react";
import { motion } from "motion/react";
import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import EmptyState from "./EmptyState";
import XPostCard from "./XPostCard";
import TickerLabel from "./TickerLabel";
import Segmented from "./Segmented";
import MenuButton, { MenuItem, MenuLabel } from "./MenuButton";
import Tooltip from "./Tooltip";
import { sourceStale } from "../lib/sources";
import { SORT_COLUMNS, sourceCounts, unifyFeed } from "../lib/newsSources";
import { useSortableRows } from "../hooks/useSortableRows";
import { prefersReducedMotion, staggerContainer, staggerItem } from "../lib/motionConfig";
import { formatRelativeTime } from "../lib/format";
import styles from "./NewsPanel.module.css";

const COMPACT_LIMIT = 5;
// GDELT can return a hundred-plus distinct domains. Showing every chip buries
// the feed, so the rail leads with the most prolific sources and expands.
const SOURCE_CHIP_LIMIT = 12;
// Same problem one level up: every tagged ticker used to get its own tab, so a
// busy news day produced a wall of chips. The rest move into an overflow menu.
const TICKER_TAB_LIMIT = 8;

function SkeletonItems({ rows = 6 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <li key={i} className={styles.item}>
      <div className={styles.body}>
        <Skeleton w="80%" h="15px" />
        <Skeleton w="40%" h="11px" />
      </div>
    </li>
  ));
}

function Article({ a, onPickSource }) {
  return (
    <>
      {a.image ? (
        <img className={styles.thumb} src={a.image} alt="" loading="lazy" />
      ) : (
        <span className={styles.thumbFallback}><Icon name="news" size={18} /></span>
      )}
      <div className={styles.body}>
        <Tooltip truncate content={a.title}>
          <a className={styles.headline} href={a.url} target="_blank" rel="noreferrer">
            {a.title}
          </a>
        </Tooltip>
        <div className={styles.meta}>
          {a.ticker && <span className={styles.tickerBadge}>{a.ticker}</span>}
          <Tooltip content={`Show only ${a.domain}`}>
            <button
              className={styles.domainBtn}
              onClick={() => onPickSource(String(a.domain || "").replace(/^www\./i, "").toLowerCase())}
            >
              {a.domain}
            </button>
          </Tooltip>
          {a.sourcecountry && <span className={styles.sep}>·</span>}
          {a.sourcecountry && <span>{a.sourcecountry}</span>}
          <span className={styles.sep}>·</span>
          <span>{formatRelativeTime(a.seendate)}</span>
        </div>
      </div>
    </>
  );
}

function SortButton({ label, sortKey, sort, onSort }) {
  const active = sort?.key === sortKey;
  return (
    <Tooltip content={active
      ? `Sorted by ${label}, ${sort.dir === "asc" ? "ascending" : "descending"} — click to reverse`
      : `Sort by ${label}`}>
      <button
        type="button"
        className={styles.sortBtn}
        data-active={active ? "yes" : "no"}
        aria-pressed={active}
        onClick={onSort}
      >
        {label}
        <span className={styles.caret} aria-hidden="true">
          {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </Tooltip>
  );
}

export default function NewsPanel({ news = [], portfolio = [], xPosts = [], sources = [], loading, busy, onRefresh, onUpdateNews, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const held = useMemo(() => new Set(portfolio.map((h) => h.ticker)), [portfolio]);

  // Articles and X posts as one list, so "source" means the same thing for both
  // and a publisher can be compared against an individual X account.
  const items = useMemo(() => unifyFeed(news, xPosts), [news, xPosts]);

  // Ranked, not alphabetical: with one chip per tagged ticker the strip grew
  // unbounded and wrapped into a wall of equally-weighted options. Holdings
  // first, then whatever has the most coverage — so the tickers worth clicking
  // are the ones that stay visible above the overflow cut.
  const tickers = useMemo(() => {
    const counts = new Map();
    for (const i of items) {
      for (const t of i.tickers) counts.set(t, (counts.get(t) || 0) + 1);
    }
    return [...counts.entries()]
      .map(([ticker, count]) => ({ ticker, count }))
      .sort((a, b) => (
        (held.has(a.ticker) ? 0 : 1) - (held.has(b.ticker) ? 0 : 1)
        || b.count - a.count
        || a.ticker.localeCompare(b.ticker)
      ));
  }, [items, held]);
  const hasTagged = tickers.length > 0;

  const [filter, setFilter] = useState(null);   // topic; null = auto
  const [source, setSource] = useState(null);   // null = every source
  const [allSources, setAllSources] = useState(false);
  const active = filter ?? (hasTagged && held.size > 0 ? "portfolio" : "all");

  // Fixed segments, then the top-ranked tickers; the tail goes into a menu so
  // the strip stays one readable row instead of growing with the feed.
  const { segments, overflowTickers } = useMemo(() => {
    const head = tickers.slice(0, TICKER_TAB_LIMIT);
    const tail = tickers.slice(TICKER_TAB_LIMIT);
    // Keep the current selection on the strip even if it ranks below the cut,
    // otherwise selecting from the overflow menu makes the active tab vanish.
    const selectedInTail = tail.find((t) => t.ticker === active);
    const shown = selectedInTail ? [...head, selectedInTail] : head;
    return {
      segments: [
        { value: "all", label: "All" },
        ...(hasTagged ? [{ value: "portfolio", label: "Portfolio" }] : []),
        { value: "macro", label: "Macro" },
        ...shown.map((t) => ({
          value: t.ticker,
          label: t.ticker,
          badge: t.count,
          title: `${t.count} item${t.count === 1 ? "" : "s"} mentioning ${t.ticker}`,
        })),
      ],
      overflowTickers: selectedInTail
        ? tail.filter((t) => t.ticker !== active)
        : tail,
    };
  }, [tickers, hasTagged, active]);

  // If GDELT hasn't responded in 24h its articles are stale; fall back to the X
  // feed, which is the only live option. Signals, not stale data.
  const newsStatus = useMemo(() => sources.find((s) => s.source === "gdelt"), [sources]);
  const xStatus = useMemo(() => sources.find((s) => s.source === "x_posts"), [sources]);
  const newsStale = sourceStale(newsStatus, 24);

  // Fetch-now state. `attempted` gates the outcome line so it only appears
  // after the user actually asked for an update.
  const [attempted, setAttempted] = useState(false);
  const fetching = Boolean(busy && attempted);

  const update = () => {
    if (!onUpdateNews) return;
    setAttempted(true);
    Promise.resolve(onUpdateNews()).catch(() => {});
  };

  // What to say once the refresh lands. The button must never imply success it
  // didn't get: GDELT rate-limits hard and is frequently in cooldown or timing
  // out, and silently doing nothing is exactly the behaviour being fixed.
  const updateOutcome = useMemo(() => {
    if (!attempted || fetching) return null;
    const failed = [
      ["News", newsStatus],
      ["X Watch", xStatus],
    ].filter(([, s]) => s && String(s.status).startsWith("error"));
    if (failed.length === 0) {
      return { tone: "ok", text: `Updated · ${newsStatus?.record_count ?? 0} articles held` };
    }
    return {
      tone: "error",
      text: failed
        .map(([label, s]) => `${label}: ${String(s.status).replace(/^error:\s*/, "")}`)
        .join(" · "),
    };
  }, [attempted, fetching, newsStatus, xStatus]);

  const byTopic = useMemo(() => {
    let rows = items;
    if (newsStale) rows = rows.filter((i) => i.kind === "x");
    if (active === "macro") return rows.filter((i) => i.tickers.length === 0);
    if (active === "portfolio") {
      return rows.filter((i) => i.tickers.some((t) => held.size === 0 || held.has(t)));
    }
    if (active !== "all") return rows.filter((i) => i.tickers.includes(active));
    return rows;
  }, [items, active, held, newsStale]);

  // Counts come from the topic-filtered set, so each number describes what
  // clicking that source would actually show.
  const counts = useMemo(() => sourceCounts(byTopic), [byTopic]);

  // Always keep the selected source visible, even when it's outside the top N.
  const shownSources = useMemo(() => {
    if (allSources || counts.length <= SOURCE_CHIP_LIMIT) return counts;
    const head = counts.slice(0, SOURCE_CHIP_LIMIT);
    if (source && !head.some((s) => s.source === source)) {
      const picked = counts.find((s) => s.source === source);
      if (picked) return [...head, picked];
    }
    return head;
  }, [counts, allSources, source]);
  const hiddenSources = counts.length - Math.min(counts.length, SOURCE_CHIP_LIMIT);

  const filtered = useMemo(
    () => (source ? byTopic.filter((i) => i.source === source) : byTopic),
    [byTopic, source],
  );

  const { sorted, sort, sortProps } = useSortableRows(
    filtered, SORT_COLUMNS, { key: "when", dir: "desc" },
  );

  const showEmpty = !loading && items.length === 0;
  const rows = compact ? sorted.slice(0, COMPACT_LIMIT) : sorted;

  const pickSource = (s) => setSource((cur) => (cur === s ? null : s));

  return (
    <section className={styles.panel} id="news">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="News" />}
        <div>
          <h2 className={styles.title}>News</h2>
          <p className={styles.subtitle}>
            Your portfolio&apos;s tape plus macro headlines, from GDELT and the X accounts you follow
          </p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
        {!compact && onUpdateNews && (
          <span className={styles.headTools}>
            <Tooltip side="bottom" content={fetching
              ? "Fetching headlines — wait for this request to finish"
              : "Fetch headlines now, bypassing the daily gate"}>
              <button
                type="button"
                className={styles.updateBtn}
                onClick={update}
                disabled={fetching}
              >
                <Icon name="refresh" size={13} />
                {fetching ? "Fetching…" : "Update"}
              </button>
            </Tooltip>
          </span>
        )}
      </header>

      {/* The outcome of an explicit Update, said out loud. GDELT rate-limits
          hard and is often in cooldown or timing out; reporting nothing would
          leave the button looking like it silently did nothing. */}
      {!collapsed && updateOutcome && (
        <p className={styles.updateNote} data-tone={updateOutcome.tone} role="status">
          <Icon name={updateOutcome.tone === "ok" ? "info" : "bell"} size={13} />
          {updateOutcome.text}
        </p>
      )}

      {!collapsed && newsStale && (
        <p className={styles.staleNote}>
          <Icon name="info" size={13} /> News hasn’t responded in the last 24h — showing X&nbsp;Watch only.
        </p>
      )}

      {!collapsed && !compact && !showEmpty && (
        <>
          <div className={styles.tabs}>
            <Segmented
              ariaLabel="News filter"
              value={active}
              onChange={setFilter}
              options={segments}
            />
            {overflowTickers.length > 0 && (
              <MenuButton label={`${overflowTickers.length} more tickers`} className={styles.moreBtn}>
                {(close) => (
                  <>
                    <MenuLabel>More tickers</MenuLabel>
                    {overflowTickers.map((t) => (
                      <MenuItem key={t.ticker} onSelect={() => { close(); setFilter(t.ticker); }}>
                        <TickerLabel ticker={t.ticker} /> <span className={styles.menuCount}>{t.count}</span>
                      </MenuItem>
                    ))}
                  </>
                )}
              </MenuButton>
            )}
          </div>

          <div className={styles.sourceBar}>
            <div className={styles.sources} role="group" aria-label="Filter by source">
              <span className={styles.sourceLabel}>Source</span>
              <button
                type="button"
                className={styles.sourceChip}
                data-active={source === null ? "yes" : "no"}
                onClick={() => setSource(null)}
              >
                All <span className={styles.count}>{byTopic.length}</span>
              </button>
              {shownSources.map((s) => (
                <Tooltip key={s.source}
                         content={`${s.kind === "x" ? `X account ${s.source}` : `Publisher ${s.source}`} — ${s.count} item${s.count === 1 ? "" : "s"}; click to show only these`}>
                  <button
                    type="button"
                    className={styles.sourceChip}
                    data-active={source === s.source ? "yes" : "no"}
                    data-kind={s.kind}
                    onClick={() => pickSource(s.source)}
                  >
                    {s.source} <span className={styles.count}>{s.count}</span>
                  </button>
                </Tooltip>
              ))}
              {hiddenSources > 0 && (
                <button
                  type="button"
                  className={styles.moreSources}
                  onClick={() => setAllSources((v) => !v)}
                >
                  {allSources ? "show fewer" : `+${hiddenSources} more`}
                </button>
              )}
            </div>

            <div className={styles.sortGroup} role="group" aria-label="Sort">
              <SortButton label="Source" sortKey="source" sort={sort} onSort={sortProps("source").onSort} />
              <SortButton label="Time" sortKey="when" sort={sort} onSort={sortProps("when").onSort} />
            </div>
          </div>
        </>
      )}

      {!collapsed && (showEmpty ? (
        <EmptyState icon="news" title="No news loaded yet" onRetry={onRefresh} busy={busy} />
      ) : (
        <motion.ul
          className={styles.list}
          variants={loading ? undefined : staggerContainer}
          initial={loading || prefersReducedMotion() ? false : "hidden"}
          animate="visible"
        >
          {loading ? (
            <SkeletonItems />
          ) : rows.length === 0 ? (
            <li className={styles.noMatch}>
              Nothing matches this filter yet — more arrives with the next refresh.
            </li>
          ) : (
            rows.map((it) => (
              <motion.li
                key={it.id}
                className={it.kind === "x" ? styles.xItem : styles.item}
                data-source={it.source}
                data-kind={it.kind}
                variants={staggerItem}
              >
                {it.kind === "x"
                  ? <XPostCard post={it.raw} compact />
                  : <Article a={it.raw} onPickSource={pickSource} />}
              </motion.li>
            ))
          )}
        </motion.ul>
      ))}
    </section>
  );
}
