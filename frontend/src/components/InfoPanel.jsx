import Icon from "./Icon";
import SourceGuide from "./SourceGuide";
import UpdatesSection from "./UpdatesSection";
import GLOSSARY from "../lib/glossary";
import { MODULE_SOURCE, SOURCE_META, sourceState } from "../lib/sources";
import { prefersReducedMotion } from "../lib/motionConfig";
import { isUpdateAvailable, useUpdateStatus } from "../hooks/useUpdateStatus";
import styles from "./InfoPanel.module.css";

// Resolves a module card's data source into a display line + live status dot state.
function cardSource(key, statusByName) {
  const entry = MODULE_SOURCE[key];
  if (!entry) return null;
  if (entry.note) return { text: entry.note, state: "none" };
  const meta = SOURCE_META[entry];
  const status = statusByName.get(entry);
  return {
    text: `Source: ${meta?.provider || entry}`,
    state: status ? sourceState(status.status) : "idle",
  };
}

// Grouped, scannable module reference. One idea per card, consistent
// What / Why / Boom Score-signal structure. Weights mirror WEIGHTS in
// backend/app/sources/boom_score.py and CHIP_META in BoomScorePanel.jsx.
const GROUPS = [
  {
    heading: "The composite",
    modules: [
      {
        key: "boom-score", icon: "spark", name: "Boom Score",
        what: "One number (typically −60 to +120) that sums every other signal below; crossing +60 fires the boom alert.",
        why: "Your at-a-glance ranking. Higher means more independent signals are pointing up right now — evidence to dig into, not a prediction.",
        signal: "The score itself — every component that fired is listed so you can see why.",
      },
    ],
  },
  {
    heading: "Insiders & officials",
    modules: [
      {
        key: "trades", icon: "trending", name: "Insider Trades (SEC Form 4)",
        what: "Open-market buys and sells filed by company executives and directors.",
        why: "The people running the company voting with their own money is one of the highest-conviction signals there is.",
        signal: "Cluster buy (≥2 in 30 days) +20 · Cluster sell −20.",
      },
      {
        key: "congress", icon: "layers", name: "Congressional Trades",
        what: "Stock transactions disclosed by members of Congress.",
        why: "Legislators sometimes trade around information and policy. Useful as a slower, longer-horizon tell.",
        signal: "Buy up to +15 (scaled by dollar size & recency) · Sale −15.",
      },
    ],
  },
  {
    heading: "Technicals & market structure",
    modules: [
      {
        key: "signals", icon: "spark", name: "Technical Signals",
        what: "Trend, momentum and volume readings: moving-average crosses, RSI, MACD, relative volume, 52-week highs.",
        why: "Captures what price and participation are actually doing right now, independent of any narrative.",
        signal: "Golden/death cross ±20 · RSI zone +10 / overbought −10 · MACD +10 · Volume-confirmed +10 · Near 52w high +10.",
      },
      {
        key: "short", icon: "trending", name: "Short Interest",
        what: "How much of the float is sold short, and squeeze potential.",
        why: "Crowded short bets can violently reverse upward when price rises, forcing buy-backs.",
        signal: "Squeeze flag +10.",
      },
    ],
  },
  {
    heading: "Crowd & coverage",
    modules: [
      {
        key: "social", icon: "news", name: "WSB / Social Sentiment",
        what: "Rising Reddit/WallStreetBets mention rank over the last day.",
        why: "Retail attention can front-run momentum and volume. A fast-rising rank is the part worth watching.",
        signal: "Rank rising sharply +10.",
      },
      {
        key: "x", icon: "x", name: "X Watch",
        what: "Recent posts from monitored X (Twitter) accounts, with detected cashtags.",
        why: "Market-moving accounts can front-run the tape. Tagged against your tickers for quick scanning.",
        signal: "Context only — flagged as unofficial when no X API key is set.",
      },
      {
        key: "analyst", icon: "star", name: "Analyst Ratings",
        what: "Wall Street upgrades, downgrades and upcoming earnings dates.",
        why: "Rating changes move institutional flows; earnings dates flag near-term event risk.",
        signal: "Upgrade +15 · Cluster of downgrades −15 · Earnings-within-7-days warning.",
      },
      {
        key: "news", icon: "news", name: "News",
        what: "Recent headlines for your tickers (via GDELT).",
        why: "Context for why a signal is firing. Read it before acting on any score.",
        signal: "Context only — does not change the Boom Score.",
      },
    ],
  },
  {
    heading: "Macro & timing",
    modules: [
      {
        key: "yield-curve", icon: "trending", name: "Yield Curve",
        what: "The spread between short- and long-term Treasury yields.",
        why: "An inversion has historically preceded recessions; the return to normal has preceded recoveries 6–18 months out.",
        signal: "Recent un-inversion +15.",
      },
      {
        key: "fear-greed", icon: "sun", name: "Fear & Greed Index",
        what: "CNN's 0–100 market-mood gauge.",
        why: "Extreme fear has historically marked good contrarian entry points; extreme greed marks froth.",
        signal: "Extreme fear (<25) +10 · Extreme greed (>78) −10.",
      },
      {
        key: "seasonality", icon: "calendar", name: "Seasonality",
        what: "A ticker's historical tendency for this exact time of year.",
        why: "Some names show a repeatable calendar edge. Shown with the average move and win rate so it's never a black box.",
        signal: "Strong seasonal tailwind (avg ≥ +2%, win-rate ≥ 60% over ~10y) +10.",
      },
    ],
  },
  {
    heading: "Catalysts & health",
    modules: [
      {
        key: "contracts", icon: "contract", name: "Federal Contracts",
        what: "Biggest recent U.S. government awards, live from USASpending.gov.",
        why: "A large new contract is real, booked future revenue — a concrete catalyst, not speculation.",
        signal: "Major award (>$100M, last 30 days) +10.",
      },
      {
        key: "fundamentals", icon: "star", name: "Fundamentals",
        what: "Core company health metrics and the next earnings date.",
        why: "Grounds the technical and sentiment signals in the actual business.",
        signal: "Feeds the earnings-soon warning that flags event risk on a high score.",
      },
    ],
  },
  {
    heading: "Your tools",
    modules: [
      {
        key: "watchlist", icon: "star", name: "Watchlist",
        what: "The tickers everything else is computed for.",
        why: "Add a symbol here and every source starts collecting signals for it.",
        signal: "Defines the universe — no direct score.",
      },
      {
        key: "portfolio", icon: "trending", name: "Portfolio",
        what: "Your actual holdings and cost basis.",
        why: "Lets the dashboard tailor suggestions and alerts to what you own.",
        signal: "Personalizes Suggestions — no direct score.",
      },
      {
        key: "suggestions", icon: "spark", name: "Suggestions",
        what: "A pre-market digest of ideas tailored to your portfolio and watchlist.",
        why: "Turns the raw signals into a short, actionable list — the same digest that can be emailed/texted.",
        signal: "Derived from Boom Score, holdings and seasonality.",
      },
      {
        key: "alerts", icon: "bell", name: "Alerts",
        what: "Notifications when a ticker crosses a meaningful threshold.",
        why: "So you don't have to watch the dashboard all day to catch a change.",
        signal: "Fires on Boom Score state changes — no direct score.",
      },
    ],
  },
];

const SECTIONS = [
  { id: "info-modules", label: "Modules" },
  { id: "info-sources", label: "Data sources" },
  { id: "info-glossary", label: "Glossary" },
  { id: "info-updates", label: "Updates" },
];

/**
 * Consolidated Info page: the module guide, the live data-source directory
 * (with expandable error diagnostics), and the glossary — with a sticky
 * in-page section nav.
 */
export default function InfoPanel({ onNavigate, sources }) {
  const statusByName = new Map((sources || []).map((s) => [s.source, s]));
  const { data: updateData } = useUpdateStatus();
  const updateAvailable = isUpdateAvailable(updateData);

  const jump = (id) => {
    document.getElementById(id)?.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "start",
    });
  };

  return (
    <section className={styles.panel} id="info">
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Info</h2>
          <p className={styles.subtitle}>
            What each panel shows, where its data comes from, and the words behind the numbers.
            Signals, not predictions — every value on this dashboard shows its source and reasoning.
          </p>
        </div>
      </header>

      <nav className={styles.sectionNav} aria-label="Info sections">
        {SECTIONS.map((s) => (
          <button key={s.id} type="button" className={styles.chip} onClick={() => jump(s.id)}>
            {s.label}
            {s.id === "info-updates" && updateAvailable && (
              <span className={styles.chipDot} role="img" aria-label="update available" />
            )}
          </button>
        ))}
      </nav>

      <div className={styles.body}>
        {/* ---- Modules ---- */}
        <div id="info-modules" className={styles.section}>
          <div className={styles.intro}>
            <h3 className={styles.introTitle}>How this dashboard thinks</h3>
            <p className={styles.introText}>
              Each module gathers one kind of <strong>public</strong> signal. The Boom Score simply adds
              the bullish ones up and subtracts the bearish ones, so a high score means several
              independent sources agree right now. Nothing here is a forecast — it's a fast way to spot
              "something is happening here," with the receipts attached. If a source can't be reached it
              shows an error rather than inventing data.
            </p>
          </div>

          {GROUPS.map((group) => (
            <div key={group.heading} className={styles.group}>
              <h3 className={styles.groupTitle}>{group.heading}</h3>
              <div className={styles.cards}>
                {group.modules.map((m) => {
                  const src = cardSource(m.key, statusByName);
                  return (
                    <button
                      key={m.key}
                      type="button"
                      className={styles.card}
                      onClick={() => onNavigate?.(m.key)}
                    >
                      <span className={styles.cardHead}>
                        <span className={styles.cardIcon}><Icon name={m.icon} size={18} /></span>
                        <span className={styles.cardName}>{m.name}</span>
                      </span>
                      <span className={styles.what}>{m.what}</span>
                      <span className={styles.why}>{m.why}</span>
                      <span className={styles.signal}>{m.signal}</span>
                      {src && (
                        <span className={styles.cardSource} data-state={src.state}>
                          <span className={styles.sourceDot} />
                          {src.text}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* ---- Data sources ---- */}
        <div id="info-sources" className={styles.section}>
          <h3 className={styles.sectionTitle}>Data sources</h3>
          <SourceGuide sources={sources} />
        </div>

        {/* ---- Glossary ---- */}
        <div id="info-glossary" className={styles.section}>
          <h3 className={styles.sectionTitle}>Glossary</h3>
          <dl className={styles.glossary}>
            {Object.values(GLOSSARY).map((g) => (
              <div key={g.label} className={styles.term}>
                <dt className={styles.termLabel}>{g.label}</dt>
                <dd className={styles.termDef}>{g.long}</dd>
              </div>
            ))}
          </dl>
        </div>

        {/* ---- Updates ---- */}
        <div id="info-updates" className={styles.section}>
          <h3 className={styles.sectionTitle}>Updates</h3>
          <UpdatesSection />
        </div>
      </div>
    </section>
  );
}
