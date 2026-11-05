import { useState } from "react";
import Icon from "./Icon";
import SelectMenu from "./SelectMenu";
import TelegramCard from "./TelegramCard";
import FxWatchEditor from "./FxWatchEditor";
import Tooltip from "./Tooltip";
import { useProfile } from "../hooks/useProfile";
import { initialsFor, gradientFor } from "../lib/avatar";
import { sendTestSuggestions } from "../api";
import { currencySymbol, formatMoney } from "../lib/format";
import styles from "./SettingsPanel.module.css";

function formatMemberSince(iso) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric", month: "long", day: "numeric",
    });
  } catch {
    return iso;
  }
}

const WINDOW_OPTIONS = [
  { key: "fwd_day", label: "Next Day", desc: "Move on the day around today's date" },
  { key: "fwd_week", label: "Next Week", desc: "Move over the ~7 days after today" },
  { key: "fwd_month", label: "Next Month", desc: "Move over the ~30 days after today" },
  { key: "cal_week", label: "This Calendar Week", desc: "Move during this calendar week" },
  { key: "cal_month", label: "This Calendar Month", desc: "Move during this calendar month" },
];

const NAME_DISPLAY_OPTIONS = [
  { value: "ticker", label: "Symbols (NVDA)" },
  { value: "company", label: "Company names (NVIDIA Corporation)" },
];

// Sections of the analysis page's company block, in render order.
const COMPANY_SECTIONS = [
  { key: "profile", label: "Profile", desc: "Name, sector, business summary, HQ, headcount, market cap." },
  { key: "valuation", label: "Valuation", desc: "P/E, forward P/E, PEG, price-to-book, growth and margin." },
  { key: "holders", label: "Ownership", desc: "Insider and institutional stakes, plus the largest holders." },
  { key: "officers", label: "Leadership", desc: "The officers the company reports, with title and pay." },
  { key: "insiders", label: "Insider trades", desc: "SEC Form 4 filings for this ticker." },
];
const DEFAULT_COMPANY_SECTIONS = Object.fromEntries(
  COMPANY_SECTIONS.map((s) => [s.key, true]),
);

const LOOKBACK_OPTIONS = [
  { value: 5, label: "5 years" },
  { value: 10, label: "10 years" },
  { value: "all", label: "All years" },
];

const TZ_OPTIONS = [
  { value: "Asia/Jerusalem", label: "Israel (Asia/Jerusalem)" },
  { value: "America/New_York", label: "New York (America/New_York)" },
  { value: "Europe/London", label: "London (Europe/London)" },
  { value: "UTC", label: "UTC" },
];

const QUOTE_INTERVALS = [
  { value: 15, label: "15 seconds" },
  { value: 30, label: "30 seconds" },
  { value: 60, label: "1 minute" },
  { value: 120, label: "2 minutes" },
];

// X handles: 1–15 letters, digits or underscore (Twitter's own rule).
const X_HANDLE_RE = /^[A-Za-z0-9_]{1,15}$/;

function formatNextRun(iso, tz) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", timeZone: tz, timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}

export default function SettingsPanel({ settings, setSetting, onNavigate, appSettingsApi, user, theme, onSetTheme, themes = [] }) {
  const activeWindows = settings.seasonalityWindows;
  const lookback = settings.seasonalityLookback;
  const memberSince = formatMemberSince(user?.created_at);

  const { profile, update, save } = useProfile();
  const base = profile.base_currency || "USD";
  // app_settings PUT is admin-only server-side; say so instead of letting a
  // non-admin edit and have the save bounce.
  const isAdmin = Boolean(user?.is_admin);
  const adminTip = isAdmin ? null : "Only an admin can change this";
  const [saveState, setSaveState] = useState(null); // null | "saving" | "saved" | error string
  const [testState, setTestState] = useState(null); // null | "sending" | results[]

  const { appSettings, update: updateApp, save: saveApp } = appSettingsApi;
  const [schedState, setSchedState] = useState(null); // null | "saving" | "saved" | error string

  // X Watch monitored accounts (server-side app setting; admin-editable).
  const xAccounts = appSettings.x_accounts || [];
  const [xInput, setXInput] = useState("");
  const [xState, setXState] = useState(null); // null | "saving" | "saved" | error string

  function addXAccount() {
    const handle = xInput.trim().replace(/^@/, "");
    if (!handle) return;
    if (!X_HANDLE_RE.test(handle)) {
      setXState("handles are 1–15 letters, digits or _");
      return;
    }
    if (xAccounts.some((a) => a.toLowerCase() === handle.toLowerCase())) {
      setXInput("");
      return;
    }
    updateApp({ x_accounts: [...xAccounts, handle] });
    setXInput("");
    setXState(null);
  }

  function removeXAccount(handle) {
    updateApp({ x_accounts: xAccounts.filter((a) => a !== handle) });
  }

  async function saveXAccounts() {
    setXState("saving");
    try {
      await saveApp({ x_accounts: appSettings.x_accounts });
      setXState("saved");
    } catch (err) {
      setXState(err.message || "could not save");
    }
  }

  async function saveSchedule() {
    setSchedState("saving");
    try {
      await saveApp({
        analysis_time: appSettings.analysis_time,
        analysis_tz: appSettings.analysis_tz,
        quotes_refresh_seconds: appSettings.quotes_refresh_seconds,
      });
      setSchedState("saved");
    } catch (err) {
      setSchedState(err.message || "could not save");
    }
  }

  const nextRun = formatNextRun(appSettings.next_analysis_run, appSettings.analysis_tz);
  const tzOptions = TZ_OPTIONS.some((o) => o.value === appSettings.analysis_tz)
    ? TZ_OPTIONS
    : [{ value: appSettings.analysis_tz, label: appSettings.analysis_tz }, ...TZ_OPTIONS];

  const toggleWindow = (key) => {
    const next = activeWindows.includes(key)
      ? activeWindows.filter((k) => k !== key)
      : [...activeWindows, key];
    setSetting("seasonalityWindows", next);
  };

  async function saveProfile() {
    setSaveState("saving");
    try {
      await save(profile);
      setSaveState("saved");
    } catch (err) {
      setSaveState(err.message || "could not save");
    }
  }

  async function sendTest() {
    setTestState("sending");
    try {
      const { results } = await sendTestSuggestions();
      setTestState(results);
    } catch (err) {
      setTestState([{ channel: "error", status: err.message || "failed" }]);
    }
  }

  return (
    <section className={styles.panel} id="settings">
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Settings</h2>
          <p className={styles.subtitle}>
            Personalize the seasonality view and reading comfort. Changes apply instantly and are
            saved on this device.
          </p>
        </div>
      </header>

      <div className={styles.body}>
        {/* Account: who's signed in */}
        {user && (
          <fieldset className={styles.group}>
            <legend className={styles.legend}>Account</legend>
            <div className={styles.account}>
              <span
                className={styles.accountAvatar}
                style={{ background: gradientFor(user.email || "") }}
                aria-hidden="true"
              >
                {initialsFor(user.email || "")}
              </span>
              <div className={styles.accountMeta}>
                <div className={styles.accountEmailRow}>
                  <span className={styles.accountEmail}>{user.email}</span>
                  {user.is_admin && <span className={styles.adminBadge}>Admin</span>}
                </div>
                {memberSince && (
                  <span className={styles.accountSince}>Member since {memberSince}</span>
                )}
              </div>
            </div>
          </fieldset>
        )}

        {/* Analysis schedule + data refresh (server-side; drives the cron job) */}
        <fieldset className={styles.group}>
          <legend className={styles.legend}>Analysis schedule &amp; data refresh</legend>
          <p className={styles.groupHint}>
            Every weekday at this time, the dashboard force-refreshes all sources, recomputes every
            holding&apos;s analysis and Boom Scores, and sends the daily digest. Live prices poll on
            their own faster cadence.
          </p>
          <div className={styles.contact}>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="analysis-time">Daily analysis time</label>
              <input
                id="analysis-time"
                className={styles.input}
                type="time"
                value={appSettings.analysis_time}
                disabled={!isAdmin}
                aria-describedby={isAdmin ? undefined : "admin-only-note"}
                onChange={(e) => updateApp({ analysis_time: e.target.value })}
              />
            </div>
            <div className={styles.field}>
              {/* SelectMenu, not a native <select>: the native option list is
                  OS chrome and ignores the app theme. */}
              <span className={styles.fieldLabel} aria-hidden="true">Timezone</span>
              <Tooltip content={adminTip}>
                <span className={styles.selectWrap} data-disabled={isAdmin ? "no" : "yes"}>
                  <SelectMenu
                    label="Timezone"
                    value={appSettings.analysis_tz}
                    options={tzOptions}
                    onChange={(v) => isAdmin && updateApp({ analysis_tz: v })}
                  />
                </span>
              </Tooltip>
            </div>
            <div className={styles.field}>
              <span className={styles.fieldLabel} aria-hidden="true">Live price refresh</span>
              <Tooltip content={adminTip}>
                <span className={styles.selectWrap} data-disabled={isAdmin ? "no" : "yes"}>
                  <SelectMenu
                    label="Live price refresh"
                    value={Number(appSettings.quotes_refresh_seconds)}
                    options={QUOTE_INTERVALS}
                    onChange={(v) => isAdmin && updateApp({ quotes_refresh_seconds: Number(v) })}
                  />
                </span>
              </Tooltip>
            </div>
          </div>
          <div className={styles.actions}>
            <Tooltip content={adminTip}>
              <button type="button" className={styles.primaryBtn} onClick={saveSchedule}
                      disabled={!isAdmin || schedState === "saving"}>
                {schedState === "saving" ? "Saving…" : "Save schedule"}
              </button>
            </Tooltip>
            {!isAdmin && (
              <span id="admin-only-note" className={styles.adminNote}>Only an admin can change the schedule.</span>
            )}
            {schedState === "saved" && <span className={styles.ok}>Saved ✓</span>}
            {schedState && schedState !== "saving" && schedState !== "saved" && (
              <span className={styles.err}>{schedState}</span>
            )}
          </div>
          {nextRun && (
            <p className={styles.note}>
              <Icon name="spark" size={14} />
              Next scheduled analysis run: <strong>{nextRun}</strong>
            </p>
          )}
        </fieldset>

        {/* X Watch monitored accounts (server-side; drives the x_posts source) */}
        <fieldset className={styles.group}>
          <legend className={styles.legend}>X Watch accounts</legend>
          <p className={styles.groupHint}>
            Handles the X Watch feed monitors for market-moving posts and cashtags. Applies on the
            next refresh.{isAdmin ? "" : " Only an admin can change this list."}
          </p>
          <div className={styles.tagList}>
            {xAccounts.length === 0 && (
              <span className={styles.tagsEmpty}>No accounts yet — the feed will be empty.</span>
            )}
            {xAccounts.map((h) => (
              <span key={h} className={styles.tag}>
                @{h}
                <Tooltip content={isAdmin ? `Remove @${h}` : adminTip}>
                  <button
                    type="button"
                    className={styles.tagRemove}
                    onClick={() => removeXAccount(h)}
                    aria-label={`Remove @${h}`}
                    disabled={!isAdmin}
                  >
                    ×
                  </button>
                </Tooltip>
              </span>
            ))}
          </div>
          <div className={styles.contact}>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="x-handle">Add an @handle</label>
              <input
                id="x-handle"
                className={styles.input}
                type="text"
                placeholder="realDonaldTrump"
                value={xInput}
                disabled={!isAdmin}
                onChange={(e) => setXInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addXAccount(); } }}
              />
            </div>
            <Tooltip content={adminTip}>
              <button type="button" className={styles.ghostBtn} onClick={addXAccount} disabled={!isAdmin}>Add</button>
            </Tooltip>
          </div>
          <div className={styles.actions}>
            <Tooltip content={adminTip}>
              <button type="button" className={styles.primaryBtn} onClick={saveXAccounts}
                      disabled={!isAdmin || xState === "saving"}>
                {xState === "saving" ? "Saving…" : "Save X accounts"}
              </button>
            </Tooltip>
            {xState === "saved" && <span className={styles.ok}>Saved ✓</span>}
            {xState && xState !== "saving" && xState !== "saved" && (
              <span className={styles.err}>{xState}</span>
            )}
          </div>
        </fieldset>

        {/* Onboarding: the in-app Info page + guided tours */}
        <fieldset className={styles.group}>
          <legend className={styles.legend}>Learn the dashboard</legend>
          <p className={styles.groupHint}>
            New here? The <strong>Info</strong> page explains what every panel shows, how it feeds the
            Boom Score, and lists every data source and whether it last responded. (Data sources have
            moved to the Info page.) Each view also has a guided tour (the <strong>?</strong> button in
            the top bar) that runs automatically on first visit.
          </p>
          <button type="button" className={styles.primaryBtn} onClick={() => onNavigate?.("info")}>
            Open the Info page
          </button>{" "}
          <Tooltip content="Guided tours will auto-run again the next time you visit each view">
            <button
              type="button"
              className={styles.primaryBtn}
              onClick={() => { setSetting("toursSeen", {}); setSetting("toursReplay", true); }}
            >
              Replay all tours
            </button>
          </Tooltip>
        </fieldset>

        {/* Trading & risk: position sizing for each holding's analysis */}
        <fieldset className={styles.group}>
          <legend className={styles.legend}>Trading &amp; risk</legend>
          <p className={styles.groupHint}>
            Sizes positions in each holding's analysis: shares = (risk&nbsp;% × account) ÷ risk-per-share.
            Risk is capped at 10% per trade. The account size is in your base currency ({base} —
            switch it on the Portfolio page); for a stock in another currency it is converted at the
            live exchange rate, and left unsized if no rate is available.
          </p>
          <div className={styles.contact}>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="acct-size">Account size ({currencySymbol(base).trim()} {base})</label>
              <input
                id="acct-size"
                className={styles.input}
                type="number"
                min="0"
                step="any"
                placeholder="50000"
                value={profile.account_size ?? ""}
                onChange={(e) => update({ account_size: e.target.value === "" ? null : Number(e.target.value) })}
              />
            </div>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="risk-pct">Risk per trade (%)</label>
              <input
                id="risk-pct"
                className={styles.input}
                type="number"
                min="0.1"
                max="10"
                step="0.1"
                value={profile.risk_pct ?? 1}
                onChange={(e) => update({ risk_pct: Number(e.target.value) })}
              />
            </div>
          </div>
          <div className={styles.actions}>
            <button type="button" className={styles.primaryBtn} onClick={saveProfile} disabled={saveState === "saving"}>
              {saveState === "saving" ? "Saving…" : "Save trading settings"}
            </button>
            {profile.account_size ? (
              <span className={styles.ok}>
                Risking {formatMoney((profile.account_size * (profile.risk_pct || 1)) / 100, base, { digits: 0 })} per trade
              </span>
            ) : null}
          </div>
        </fieldset>

        {/* Currencies shown in the ticker tape (per user, server-side) */}
        <FxWatchEditor />

        {/* Notifications: email + phone for the daily suggestion digest */}
        <fieldset className={styles.group}>
          <legend className={styles.legend}>Daily suggestions — email &amp; phone</legend>
          <p className={styles.groupHint}>
            Get a few tailored ideas for the next trading day, based on your portfolio and watchlist.
            Sent pre-market on trading days. Delivery is off until you add SMTP/Twilio keys on the
            server, but you can save your contact info and preview the digest now.
          </p>

          <div className={styles.contact}>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="notify-email">Email address</label>
              <input
                id="notify-email"
                className={styles.input}
                type="email"
                placeholder="you@example.com"
                value={profile.email}
                onChange={(e) => update({ email: e.target.value })}
              />
            </div>
            <label className={styles.miniSwitch}>
              <input
                type="checkbox"
                checked={profile.email_enabled}
                onChange={(e) => update({ email_enabled: e.target.checked })}
              />
              Email me
            </label>
          </div>

          <div className={styles.contact}>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="notify-phone">Phone (E.164)</label>
              <input
                id="notify-phone"
                className={styles.input}
                type="tel"
                placeholder="+14155551234"
                value={profile.phone}
                onChange={(e) => update({ phone: e.target.value })}
              />
            </div>
            <label className={styles.miniSwitch}>
              <input
                type="checkbox"
                checked={profile.sms_enabled}
                onChange={(e) => update({ sms_enabled: e.target.checked })}
              />
              Text me
            </label>
          </div>

          <div className={styles.actions}>
            <button type="button" className={styles.primaryBtn} onClick={saveProfile} disabled={saveState === "saving"}>
              {saveState === "saving" ? "Saving…" : "Save contact info"}
            </button>
            <button type="button" className={styles.ghostBtn} onClick={sendTest} disabled={testState === "sending"}>
              {testState === "sending" ? "Sending…" : "Send test now"}
            </button>
            {saveState === "saved" && <span className={styles.ok}>Saved ✓</span>}
            {saveState && saveState !== "saving" && saveState !== "saved" && (
              <span className={styles.err}>{saveState}</span>
            )}
          </div>

          {Array.isArray(testState) && (
            <ul className={styles.testResults}>
              {testState.map((r) => (
                <li key={r.channel}>
                  <strong>{r.channel}:</strong>{" "}
                  <span data-ok={r.status === "sent" ? "yes" : "no"}>{r.status}</span>
                </li>
              ))}
            </ul>
          )}

          <p className={styles.note}>
            <Icon name="spark" size={14} />
            Add the holdings the suggestions should account for in the{" "}
            <button type="button" className={styles.link} onClick={() => onNavigate?.("portfolio")}>
              Portfolio
            </button>{" "}
            tab, and preview the exact digest in the{" "}
            <button type="button" className={styles.link} onClick={() => onNavigate?.("suggestions")}>
              Suggestions
            </button>{" "}
            tab.
          </p>
        </fieldset>

        {/* Telegram: alerts and bot fills to a linked chat, read-only commands */}
        <TelegramCard />

        {/* Seasonality windows */}
        <fieldset className={styles.group}>
          <legend className={styles.legend}>Seasonality windows</legend>
          <p className={styles.groupHint}>Which "this time of year" windows to show in the Seasonality panel.</p>
          <div className={styles.checks}>
            {WINDOW_OPTIONS.map((opt) => (
              <label key={opt.key} className={styles.check}>
                <input
                  type="checkbox"
                  checked={activeWindows.includes(opt.key)}
                  onChange={() => toggleWindow(opt.key)}
                />
                <span className={styles.checkText}>
                  <span className={styles.checkLabel}>{opt.label}</span>
                  <span className={styles.checkDesc}>{opt.desc}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {/* Lookback */}
        <fieldset className={styles.group}>
          <legend className={styles.legend}>History lookback</legend>
          <p className={styles.groupHint}>How many past years feed the averages and win-rate.</p>
          <div className={styles.segmented} role="group" aria-label="History lookback">
            {LOOKBACK_OPTIONS.map((opt) => (
              <button
                key={String(opt.value)}
                type="button"
                className={styles.segBtn}
                data-active={lookback === opt.value ? "yes" : "no"}
                aria-pressed={lookback === opt.value}
                onClick={() => setSetting("seasonalityLookback", opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </fieldset>

        {/* Appearance: color theme */}
        {onSetTheme && themes.length > 0 && (
          <fieldset className={styles.group}>
            <legend className={styles.legend}>Appearance</legend>
            <p className={styles.groupHint}>
              Pick a color theme. Applies instantly and is saved on this device.
            </p>
            <div className={styles.themeGrid} role="radiogroup" aria-label="Color theme">
              {themes.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  role="radio"
                  aria-checked={theme === t.key}
                  className={styles.themeCard}
                  data-active={theme === t.key ? "yes" : "no"}
                  onClick={() => onSetTheme(t.key)}
                >
                  <span
                    className={styles.themeCardSwatch}
                    style={{ background: `linear-gradient(135deg, ${t.swatch[0]} 0 50%, ${t.swatch[1]} 50% 100%)` }}
                    aria-hidden="true"
                  />
                  <span className={styles.themeCardLabel}>{t.label}</span>
                  <span className={styles.themeCardHint}>{t.hint}</span>
                </button>
              ))}
            </div>
          </fieldset>
        )}

        {/* Reading & focus */}
        <fieldset className={styles.group}>
          <legend className={styles.legend}>Ticker labels</legend>
          <p className={styles.groupHint}>
            How stocks are named across the dashboard. Company names come from the
            fundamentals and SEC filing data already stored — a ticker with no
            recorded name keeps showing its symbol.
          </p>
          <div className={styles.segmented} role="radiogroup" aria-label="Ticker labels">
            {NAME_DISPLAY_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={settings.nameDisplay === o.value}
                className={styles.segBtn}
                data-active={settings.nameDisplay === o.value ? "yes" : "no"}
                onClick={() => setSetting("nameDisplay", o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className={styles.group}>
          <legend className={styles.legend}>Company information</legend>
          <p className={styles.groupHint}>
            Which sections the analysis page shows for a stock. Anything a data source
            hasn&apos;t returned still reads as &ldquo;—&rdquo; rather than being filled in.
          </p>
          {COMPANY_SECTIONS.map((s) => {
            const on = settings.companyInfo?.[s.key] !== false;
            return (
              <label key={s.key} className={styles.switchRow}>
                <span className={styles.switchText}>
                  <span className={styles.checkLabel}>{s.label}</span>
                  <span className={styles.checkDesc}>{s.desc}</span>
                </span>
                <button
                  type="button"
                  className={styles.switch}
                  role="switch"
                  aria-checked={on}
                  data-on={on ? "yes" : "no"}
                  onClick={() => setSetting("companyInfo", {
                    ...DEFAULT_COMPANY_SECTIONS,
                    ...(settings.companyInfo || {}),
                    [s.key]: !on,
                  })}
                >
                  <span className={styles.knob} />
                </button>
              </label>
            );
          })}
        </fieldset>

        <fieldset className={styles.group}>
          <legend className={styles.legend}>Reading &amp; focus</legend>
          <label className={styles.switchRow}>
            <span className={styles.switchText}>
              <span className={styles.checkLabel}>Dyslexia-friendly mode</span>
              <span className={styles.checkDesc}>
                Switches to the Atkinson Hyperlegible typeface with larger text and extra letter,
                word, and line spacing.
              </span>
            </span>
            <button
              type="button"
              className={styles.switch}
              role="switch"
              aria-checked={settings.dyslexia}
              data-on={settings.dyslexia ? "yes" : "no"}
              onClick={() => setSetting("dyslexia", !settings.dyslexia)}
            >
              <span className={styles.knob} />
            </button>
          </label>

          <label className={styles.switchRow}>
            <span className={styles.switchText}>
              <span className={styles.checkLabel}>Reduce motion</span>
              <span className={styles.checkDesc}>
                Turns off chart and panel animations for a calmer, steadier screen.
              </span>
            </span>
            <button
              type="button"
              className={styles.switch}
              role="switch"
              aria-checked={settings.reducedMotion}
              data-on={settings.reducedMotion ? "yes" : "no"}
              onClick={() => setSetting("reducedMotion", !settings.reducedMotion)}
            >
              <span className={styles.knob} />
            </button>
          </label>

          <label className={styles.switchRow}>
            <span className={styles.switchText}>
              <span className={styles.checkLabel}>Focus mode (one thing at a time)</span>
              <span className={styles.checkDesc}>
                On the Overview, keep a single section open at once. Opening one closes the rest.
              </span>
            </span>
            <button
              type="button"
              className={styles.switch}
              role="switch"
              aria-checked={settings.focusMode}
              data-on={settings.focusMode ? "yes" : "no"}
              onClick={() => setSetting("focusMode", !settings.focusMode)}
            >
              <span className={styles.knob} />
            </button>
          </label>

          <p className={styles.note}>
            <Icon name="spark" size={14} />
            ADHD-friendly defaults — calm motion, clear focus outlines, generous spacing, and
            row striping — are always on for everyone.
          </p>
        </fieldset>
      </div>
    </section>
  );
}
