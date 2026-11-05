import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import Popover from "./Popover";
import Tooltip from "./Tooltip";
import TickerLabel from "./TickerLabel";
import { alertIcon, alertRoute } from "../lib/alertMeta";
import { routeToPath } from "../lib/nav";
import { formatRelativeTime } from "../lib/format";
import styles from "./AlertsBell.module.css";

/** Passive unread indicator + popover of recent alerts. No full page. */
export default function AlertsBell({ alerts = [], unread = 0, onMarkRead, onOpen }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const popRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      // Portaled to <body>, so it is not inside wrapRef — check it separately.
      if (popRef.current?.contains(e.target)) return;
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const recent = alerts.slice(0, 8);

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <Tooltip side="bottom" disabled={open}
               content={unread > 0 ? `Alerts — ${unread} unread` : "Alerts"}>
        <button
          type="button"
          className={styles.bell}
          onClick={() => setOpen((o) => !o)}
          aria-label={unread > 0 ? `Alerts, ${unread} unread` : "Alerts"}
          aria-expanded={open}
          data-unread={unread > 0 ? "yes" : "no"}
        >
          <Icon name="bell" size={17} />
          {unread > 0 && <span className={styles.badge}>{unread > 9 ? "9+" : unread}</span>}
        </button>
      </Tooltip>

      <Popover
        open={open}
        anchorRef={wrapRef}
        contentRef={popRef}
        className={styles.pop}
        role="dialog"
        aria-label="Recent alerts"
      >
          <div className={styles.popHead}>
            <span className="caption">Alerts</span>
            {unread > 0 && (
              <button type="button" className={styles.markRead} onClick={() => onMarkRead?.()}>
                Mark all read
              </button>
            )}
          </div>
          {recent.length === 0 ? (
            <p className={styles.empty}>No alerts yet. They appear as your signals change.</p>
          ) : (
            <ul className={styles.list}>
              {recent.map((a) => (
                <li key={a.dedup_key} className={styles.item} data-unread={a.read ? "no" : "yes"}>
                  <Tooltip content={`${a.severity} severity`} side="left">
                    <span className={styles.dot} data-sev={a.severity} role="img"
                          aria-label={`${a.severity} severity`} />
                  </Tooltip>
                  {/* A real anchor, not a button with onClick: that's what makes
                      Ctrl/Cmd/middle-click open a new tab, and what lets the
                      status bar preview the destination. The handler only
                      intercepts a plain left click. */}
                  <a
                    className={styles.body}
                    href={routeToPath(alertRoute(a))}
                    onClick={(e) => {
                      if (e.defaultPrevented || e.button !== 0
                          || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
                        return; // let the browser do its thing
                      }
                      e.preventDefault();
                      onOpen?.(a);
                    }}
                  >
                    <div className={styles.line1}>
                      {a.ticker
                        ? <TickerLabel ticker={a.ticker} className={styles.symbol} />
                        : <span className={styles.symbol}>Account</span>}
                      <Icon name={alertIcon(a.type)} size={13} />
                      <Tooltip truncate><span className={styles.title}>{a.title}</span></Tooltip>
                      <span className={styles.time}>{formatRelativeTime(a.created_at)}</span>
                    </div>
                    <p className={styles.message}>{a.message}</p>
                  </a>
                  {!a.read && (
                    <Tooltip content="Mark this alert read" side="left">
                      <button
                        type="button"
                        className={styles.markOne}
                        aria-label={`Mark "${a.title}" read`}
                        onClick={() => onMarkRead?.([a.dedup_key])}
                      >
                        <Icon name="check" size={13} />
                      </button>
                    </Tooltip>
                  )}
                </li>
              ))}
            </ul>
          )}
      </Popover>
    </div>
  );
}
