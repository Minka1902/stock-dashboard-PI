import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import AlertsBell from "./AlertsBell";
import Popover from "./Popover";
import UserMenu from "./UserMenu";
import Tooltip from "./Tooltip";
import { formatRelativeTime } from "../lib/format";
import { LEAN_READ, leanLabel, leanTone } from "../lib/lean";
import RegimeChip from "./RegimeChip";
import styles from "./TopBar.module.css";

// Theme switcher: a trigger button + motion popover of the available themes,
// each with a two-tone preview swatch.
function ThemeMenu({ theme, themes, onSetTheme }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const menuRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      // Portaled to <body>, so it is not inside ref — check it separately.
      if (menuRef.current?.contains(e.target)) return;
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const isLight = theme === "light" || theme === "warm";
  return (
    <div className={styles.themeWrap} ref={ref}>
      <Tooltip side="bottom" disabled={open} content="Change theme">
        <button
          className={styles.iconBtn}
          onClick={() => setOpen((o) => !o)}
          aria-label="Change theme"
          aria-haspopup="menu"
          aria-expanded={open}
          data-active={open ? "yes" : "no"}
        >
          <Icon name={isLight ? "sun" : "moon"} size={17} />
        </button>
      </Tooltip>
      <Popover open={open} anchorRef={ref} contentRef={menuRef} className={styles.themeMenu} role="menu">
            {themes.map((t) => (
              <button
                key={t.key}
                type="button"
                role="menuitemradio"
                aria-checked={t.key === theme}
                className={styles.themeItem}
                data-active={t.key === theme ? "yes" : "no"}
                onClick={() => { onSetTheme(t.key); setOpen(false); }}
              >
                <span
                  className={styles.themeSwatch}
                  style={{ background: `linear-gradient(135deg, ${t.swatch[0]} 0 50%, ${t.swatch[1]} 50% 100%)` }}
                  aria-hidden="true"
                />
                <span className={styles.themeItemText}>
                  <span className={styles.themeItemLabel}>{t.label}</span>
                  <span className={styles.themeItemHint}>{t.hint}</span>
                </span>
              </button>
            ))}
      </Popover>
    </div>
  );
}

export default function TopBar({
  title, sources, busy, onRefresh, theme, onSetTheme, themes = [],
  dyslexia, onToggleDyslexia, lean, alerts = [], unreadAlerts = 0,
  onMarkAlertsRead, onOpenAlert, onOpenCommand, user, onLogout, onNavigate, hasTour, onStartTour,
}) {
  const refreshed = sources
    .filter((s) => s.last_refreshed_at)
    .sort((a, b) => (a.last_refreshed_at < b.last_refreshed_at ? 1 : -1));
  const latest = refreshed[0];
  const live = latest && (latest.status === "ok" || latest.status.startsWith("ok ("));
  const lastRefresh = latest ? formatRelativeTime(latest.last_refreshed_at) : "never";
  const themeIndex = themes.findIndex((t) => t.key === theme);
  const nextTheme = themes[(themeIndex + 1) % Math.max(themes.length, 1)];
  // On a phone these live in the account menu instead of the bar.
  const phoneItems = [
    { id: "refresh", label: busy ? "Syncing…" : "Refresh all sources", icon: "refresh",
      run: onRefresh, disabled: busy },
    ...(nextTheme ? [{ id: "theme", label: `Theme: ${nextTheme.label}`, icon: "moon",
      run: () => onSetTheme(nextTheme.key) }] : []),
    { id: "dyslexia", label: "Dyslexia-friendly mode", icon: "book", hint: dyslexia ? "on" : "off",
      run: onToggleDyslexia },
    ...(hasTour ? [{ id: "tour", label: "Guided tour of this view", icon: "info", run: onStartTour }] : []),
  ];

  return (
    <header className={styles.bar}>
      <div className={styles.left}>
        <h1 className={styles.title}>{title}</h1>
        <Tooltip side="bottom" content={live
          ? `LIVE — the most recent source refresh (${latest.source}) succeeded ${lastRefresh}.`
          : latest
            ? `IDLE — the most recent source refresh (${latest.source}) did not succeed: ${latest.status}. See the Server page for details.`
            : "IDLE — no source has refreshed yet."}>
          <span className={styles.status} data-live={live ? "yes" : "no"} tabIndex={0}>
            <span className={styles.dot} />
            {live ? "LIVE" : "IDLE"}
            <span className={styles.since}>· {lastRefresh}</span>
          </span>
        </Tooltip>
      </div>

      <div className={styles.actions}>
        {lean && (
          <Tooltip side="bottom" content={(
            <>
              <strong>Market lean: {leanLabel(lean)}</strong>
              <p>{LEAN_READ[lean] || "Composite read of the market sentiment indicators."} It summarises where sentiment indicators sit — it is not a trade instruction.</p>
            </>
          )}>
            <span className={styles.lean} data-lean={lean} data-tone={leanTone(lean)} tabIndex={0}>
              <span className={styles.leanCap}>Lean</span>
              {leanLabel(lean)}
            </span>
          </Tooltip>
        )}

        <RegimeChip />

        <Tooltip side="bottom" content="Jump to a view, or search any stock"
                 shortcut={[["Ctrl", "K"], ["⌘", "K"]]}>
          <button
            type="button"
            className={styles.cmd}
            onClick={onOpenCommand}
            aria-label="Open command palette and stock search"
            data-tour="palette"
          >
            <Icon name="command" size={13} />
            <kbd>K</kbd>
          </button>
        </Tooltip>
        <button
          type="button"
          className={`${styles.iconBtn} ${styles.phoneOnly}`}
          onClick={onOpenCommand}
          aria-label="Search stocks and views"
        >
          <Icon name="search" size={17} />
        </button>

        <span data-tour="alerts">
          <AlertsBell
            alerts={alerts}
            unread={unreadAlerts}
            onMarkRead={onMarkAlertsRead}
            onOpen={onOpenAlert}
          />
        </span>

        {hasTour && (
          <Tooltip side="bottom" content="Take a guided tour of this view">
            <button
              type="button"
              className={`${styles.iconBtn} ${styles.desktopOnly}`}
              onClick={onStartTour}
              aria-label="Take a guided tour of this view"
            >
              <span aria-hidden="true" style={{ fontWeight: 700 }}>?</span>
            </button>
          </Tooltip>
        )}

        <Tooltip side="bottom" content={dyslexia ? "Turn off dyslexia-friendly mode" : "Turn on dyslexia-friendly mode"}>
          <button
            className={`${styles.iconBtn} ${styles.desktopOnly}`}
            onClick={onToggleDyslexia}
            aria-label="Dyslexia-friendly mode"
            aria-pressed={dyslexia}
            data-active={dyslexia ? "yes" : "no"}
          >
            <Icon name="book" size={17} />
          </button>
        </Tooltip>
        <span className={styles.desktopOnly}>
          <ThemeMenu theme={theme} themes={themes} onSetTheme={onSetTheme} />
        </span>
        <Tooltip side="bottom" content={busy
          ? "Syncing — a refresh is already running"
          : "Fetch fresh data from every source now, then reload the dashboard"}>
          <button className={`${styles.refresh} ${styles.desktopOnly}`} onClick={onRefresh} disabled={busy}
                  data-tour="refresh">
            <span className={busy ? styles.spin : ""}>
              <Icon name="refresh" size={15} />
            </span>
            {busy ? "Syncing" : "Refresh"}
          </button>
        </Tooltip>

        <UserMenu user={user} onLogout={onLogout} onNavigate={onNavigate} phoneItems={phoneItems} />
      </div>
    </header>
  );
}
