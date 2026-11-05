// Polls GET /api/alerts and raises native Windows toasts for high-severity ones.
//
// Ported from extension/src/background/index.js — the backend has no push
// channel (CLAUDE.md notes the extension polls for exactly this reason), so
// polling is the correct shape. What carried over, and why:
//
//   * the `seeded` first-run gate — without it, first launch fires a
//     notification for every one of the 100 rows already in the backlog
//   * dedup on dedup_key via seen.js, because /api/alerts returns a global,
//     cursorless page and only `read` differs per user
//   * severity === "high" && !read, mirroring the backend's own push gate
//   * a 3-per-poll cap with a "+N more" summary
//
// Deliberately NOT ported: chrome.alarms (the main process is long-lived, so a
// plain setInterval is right), chrome.storage, the boom-score/symbol caches,
// the message broker, and apiBase configurability (the origin is fixed).

import { Notification } from "electron";

import { apiGet } from "../api.js";
import { MAX_NOTIFICATIONS_PER_POLL, POLL_MS, ICON } from "../config.js";
import { diffNew, pushSeen, tickerFromKey } from "./seen.js";

export class AlertPoller {
  /**
   * @param {JsonStore} store persisted seenKeys / seeded
   * @param {(state: {unread: number, authed: boolean, reachable: boolean}) => void} onState
   * @param {(path: string) => void} onOpen deep-link handler
   */
  constructor(store, onState, onOpen) {
    this.store = store;
    this.onState = onState;
    this.onOpen = onOpen;
    this.timer = null;
  }

  start() {
    if (this.timer) return;
    this.poll();
    this.timer = setInterval(() => this.poll(), POLL_MS);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async poll() {
    let payload;
    try {
      payload = await apiGet("/api/alerts");
    } catch (err) {
      // 401 is "not signed in yet", not a failure worth surfacing loudly —
      // the window itself shows the login screen. Anything else means the
      // backend is unreachable, which the tray tooltip should say.
      this.onState({ unread: 0, authed: err.status !== 401, reachable: err.status !== undefined });
      return;
    }

    const alerts = payload?.alerts ?? [];
    const unread = Number(payload?.unread) || 0;
    this.onState({ unread, authed: true, reachable: true });

    const seenKeys = this.store.get("seenKeys") || [];
    const keys = alerts.map((a) => a.dedup_key);

    if (!this.store.get("seeded")) {
      this.store.set({ seenKeys: pushSeen([], keys), seeded: true });
      return;
    }

    const fresh = diffNew(seenKeys, alerts);
    this.store.set({ seenKeys: pushSeen(seenKeys, keys) });

    if (this.store.get("notifyHighSeverity") === false) return;
    const high = fresh.filter((a) => a.severity === "high" && !a.read);
    if (!high.length) return;

    const shown = high.slice(0, MAX_NOTIFICATIONS_PER_POLL);
    for (const a of shown) this.#notify(a);

    const rest = high.length - shown.length;
    if (rest > 0) {
      const n = new Notification({
        title: `+${rest} more high-severity alert${rest === 1 ? "" : "s"}`,
        body: "Open the dashboard to review them.",
        icon: ICON,
      });
      n.on("click", () => this.onOpen("/"));
      n.show();
    }
  }

  #notify(a) {
    if (!Notification.isSupported()) return;
    const n = new Notification({
      title: `${a.ticker} — ${a.title}`,
      body: a.message || "",
      icon: ICON,
    });
    n.on("click", () => {
      const ticker = tickerFromKey(a.dedup_key);
      // Matches the in-app openAlert() in frontend/src/App.jsx, which the
      // detail page reads to scroll to and highlight the alert.
      // There is no /alerts view — the bell on the default view is where they
      // live — so an unparseable key falls back to "/".
      this.onOpen(ticker
        ? `/stock/${ticker}?alert=${encodeURIComponent(a.dedup_key)}`
        : "/");
    });
    n.show();
  }
}
