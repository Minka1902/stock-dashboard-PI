import { useEffect, useRef } from "react";
import { motion, useMotionValue, useAnimationFrame } from "motion/react";
import { prefersReducedMotion } from "../lib/motionConfig";
import { useSettingsContext } from "../hooks/useSettingsContext";
import { formatPrice as formatMoneyPrice } from "../lib/format";
import Tooltip from "./Tooltip";
import styles from "./LiveTicker.module.css";

function tone(pct) {
  if (pct == null) return "flat";
  return pct >= 0 ? "pos" : "neg";
}

const isFx = (q) => q.kind === "fx";
const isIndex = (q) => q.kind === "index";

const CURRENCY_NAMES = {
  USD: "US dollar", ILS: "Israeli shekel", EUR: "euro", GBP: "British pound", JPY: "Japanese yen",
  CHF: "Swiss franc", CAD: "Canadian dollar", AUD: "Australian dollar",
};
const SESSION_WORD = { LIVE: "open", PRE: "pre-market", POST: "after hours", CLOSED: "closed" };

// Two decimals is right for a share price and wrong for a rate: EUR/USD would
// read a flat "1.09" and never appear to move. Equities carry their currency
// symbol (₪ for Tel Aviv listings); an index is in points, a rate is bare.
function formatPrice(q) {
  if (q.price == null) return "—";
  if (isFx(q)) return q.price.toFixed(4);
  if (isIndex(q) || !q.currency) return q.price.toFixed(2);
  return formatMoneyPrice(q.price, q.currency);
}

function tipFor(q, labelFor) {
  if (isFx(q)) {
    const [a, b] = (q.label || "").split("/");
    const an = CURRENCY_NAMES[a] || a;
    const bn = CURRENCY_NAMES[b] || b;
    return (
      <>
        <strong>{an} → {bn}</strong>
        <p>1 {a} = {q.price != null ? q.price.toFixed(4) : "—"} {b}. Currencies trade around the clock on
          weekdays, so there is no session badge. Edit these pairs in Settings.</p>
      </>
    );
  }
  const market = q.market === "TASE" ? "Tel Aviv Stock Exchange" : q.market === "US" ? "US market" : null;
  const state = q.market_state ? SESSION_WORD[q.market_state] || q.market_state.toLowerCase() : null;
  const name = isIndex(q) ? `${q.label} index` : labelFor(q.ticker);
  return (
    <>
      <strong>{q.ticker}{name && name !== q.ticker ? ` — ${name}` : ""}</strong>
      <p>
        {[market, q.currency && !isIndex(q) ? `priced in ${q.currency}` : null, state ? `session ${state}` : null]
          .filter(Boolean).join(" · ")}
      </p>
    </>
  );
}

function Item({ q }) {
  const t = tone(q.change_pct);
  const { labelFor } = useSettingsContext();
  return (
    <Tooltip content={tipFor(q, labelFor)} side="bottom">
    <span className={styles.item} role="listitem">
      <span className={styles.symbol}>{q.label || q.ticker}</span>
      <span className={styles.price}>{formatPrice(q)}</span>
      <span className={styles.change} data-tone={t}>
        <span className={styles.arrow}>{t === "pos" ? "▲" : t === "neg" ? "▼" : "•"}</span>
        {q.change_pct != null ? `${Math.abs(q.change_pct).toFixed(2)}%` : "—"}
      </span>
      {/* Session badges are an equity notion; FX has no pre/post market. */}
      {!isFx(q) && (q.market_state === "PRE" || q.market_state === "POST") && (
        <span className={styles.badge}>{q.market_state}</span>
      )}
    </span>
    </Tooltip>
  );
}

function marketBadge(quotes) {
  // US equities only: FX trades ~24/5 and reports REGULAR through the night, so
  // letting it answer here would show "LIVE" at 3am on a closed market; TASE
  // has its own chip.
  const state = quotes.find((q) => !isFx(q) && q.market !== "TASE" && q.market_state)?.market_state;
  return state || null;
}

// Marquee speed: px/sec derived so a full loop (one copy width) takes ~48s,
// matching the old CSS keyframe cadence regardless of content width.
const LOOP_SECONDS = 48;

/**
 * JS-driven infinite marquee: one motion value drives translateX, advancing
 * every frame and wrapping modulo the (duplicated) list's half-width. Hovering
 * pauses the auto-advance and hands control to drag; releasing the pointer and
 * leaving resumes the scroll from the current offset with no jump.
 */
function Marquee({ quotes }) {
  const x = useMotionValue(0);
  const trackRef = useRef(null);
  const halfRef = useRef(0);
  const pausedRef = useRef(false);    // hover
  const draggingRef = useRef(false);  // active pointer drag

  useEffect(() => {
    const measure = () => {
      const el = trackRef.current;
      if (el) halfRef.current = el.scrollWidth / 2;
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (trackRef.current) ro.observe(trackRef.current);
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); window.removeEventListener("resize", measure); };
  }, []);

  useAnimationFrame((_t, dt) => {
    const half = halfRef.current;
    if (!half) return;
    if (!pausedRef.current && !draggingRef.current) {
      const speed = half / LOOP_SECONDS;
      x.set(x.get() - (speed * dt) / 1000);
    }
    // Wrap into (-half, 0]; positions v and v±half are visually identical
    // because the list is duplicated, so this loops seamlessly. Skipped while a
    // pointer drag owns the value so we don't fight the gesture.
    if (!draggingRef.current) {
      let v = x.get();
      if (v <= -half) v += half;
      else if (v > 0) v -= half;
      x.set(v);
    }
  });

  return (
    <div
      className={styles.trackWrap}
      onMouseEnter={() => { pausedRef.current = true; }}
      onMouseLeave={() => { pausedRef.current = false; }}
    >
      <motion.div
        ref={trackRef}
        className={styles.track}
        style={{ x }}
        drag="x"
        dragMomentum={false}
        onDragStart={() => { draggingRef.current = true; }}
        onDragEnd={() => { draggingRef.current = false; }}
      >
        {quotes.map((q) => <Item key={q.ticker} q={q} />)}
        {/* duplicate for a seamless loop */}
        {quotes.map((q) => <Item key={`${q.ticker}-b`} q={q} />)}
      </motion.div>
    </div>
  );
}

/** Scrolling tape of live quotes (incl. pre/post-market). Pauses + drags on hover; static under reduce-motion. */
export default function LiveTicker({ quotes, asOf, marketStatus, marketStatuses }) {
  const reduced = prefersReducedMotion();
  if (!quotes || quotes.length === 0) return null;
  // Prefer the backend's clock-based session; fall back to per-quote state.
  const state = marketStatus || marketBadge(quotes);
  // TASE gets its own session chip whenever the tape carries a Tel Aviv item.
  const hasTase = quotes.some((q) => q.market === "TASE");
  const taseState = marketStatuses?.TASE || null;
  const stamp = asOf
    ? new Date(asOf).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;
  return (
    <div className={styles.tape} role="list" aria-label="Live quotes">
      {stamp && (
        <Tooltip content={`US market (NYSE/Nasdaq) session by the exchange clock · quotes as of ${stamp}`} side="bottom">
          <span className={styles.asOf} data-state={state || "CLOSED"} tabIndex={0}>
            <span className={styles.dot} aria-hidden="true" />
            {hasTase && taseState ? "US " : ""}{state || "CLOSED"} · {stamp}
          </span>
        </Tooltip>
      )}
      {stamp && hasTase && taseState && (
        <Tooltip content="Tel Aviv Stock Exchange session: Mon–Thu 10:00–17:35, Fri 10:00–13:50 Israel time. No pre-market or after-hours trading." side="bottom">
          <span className={styles.asOf} data-state={taseState} tabIndex={0}>
            <span className={styles.dot} aria-hidden="true" />
            TASE {taseState}
          </span>
        </Tooltip>
      )}
      {reduced ? (
        <div className={styles.trackWrap} data-static="yes">
          <div className={styles.track}>
            {quotes.map((q) => <Item key={q.ticker} q={q} />)}
            {quotes.map((q) => <Item key={`${q.ticker}-b`} q={q} />)}
          </div>
        </div>
      ) : (
        <Marquee quotes={quotes} />
      )}
    </div>
  );
}
