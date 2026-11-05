// Background worker: the only context that touches the network.
//
// MV3 discipline — this file is torn down whenever it goes idle and restarted on
// the next event, so:
//   * every listener is registered synchronously at top level,
//   * no mutable module-level state survives, so all caches live in storage,
//   * scheduling is chrome.alarms only — setInterval would die with the worker,
//   * no window / document / localStorage.
// Firefox runs this as a non-persistent event page rather than a service worker;
// the same rules make one bundle valid for both.

import { ext, createNotification, openTab } from "../lib/browser.js";
import * as api from "../lib/api.js";
import { ApiError, PERMISSION_DENIED } from "../lib/api.js";
import { MSG, ok, fail } from "../lib/messages.js";
import {
  DEFAULTS,
  getSettings,
  getLocal,
  setLocal,
  readCache,
  writeCache,
  normalizeBase,
  stockUrl,
} from "../lib/storage.js";
import { isSiteEnabled } from "../lib/sites.js";
import { diffNew, pushSeen, tickerFromKey, badgeText } from "./seen.js";

const ALARM = "poll";
const SCORES_TTL_MS = 120_000; // just under the backend's 180s recompute cadence
const SYMBOLS_TTL_MS = 24 * 60 * 60 * 1000;
const MAX_NOTIFICATIONS_PER_POLL = 3;

// --- badge ---------------------------------------------------------------

async function setBadge(text, colour, title) {
  try {
    await ext.action.setBadgeText({ text });
    if (colour) await ext.action.setBadgeBackgroundColor({ color: colour });
    if (title) await ext.action.setTitle({ title });
  } catch {
    /* action API unavailable during teardown */
  }
}

const BADGE_OK = "#7c5cff";
const BADGE_WARN = "#b4884d";

async function reflectError(err) {
  if (err?.status === 401) {
    await setLocal({ authState: "signed_out", lastError: null });
    await setBadge("!", BADGE_WARN, "Stock Signals — sign in to your dashboard");
  } else if (err?.status === PERMISSION_DENIED) {
    await setLocal({ authState: "no_permission", lastError: err.message });
    await setBadge("!", BADGE_WARN, "Stock Signals — grant access to your dashboard");
  } else {
    await setLocal({ authState: "unreachable", lastError: err?.message ?? String(err) });
    await setBadge("?", BADGE_WARN, `Stock Signals — ${err?.message ?? "unreachable"}`);
  }
}

// --- caches --------------------------------------------------------------

/** Whole boom-score table, cached so a page full of tickers costs one request. */
async function boomScores({ force = false } = {}) {
  if (!force) {
    const cached = await readCache("cache:boom", SCORES_TTL_MS);
    if (cached) return cached;
  }
  const rows = await api.getBoomScores();
  await writeCache("cache:boom", rows);
  return rows;
}

/** Known-symbol set for bare-ticker detection. Null is a valid, degraded answer. */
async function symbols() {
  const cached = await readCache("cache:symbols", SYMBOLS_TTL_MS);
  if (cached) return cached;
  const names = await api.getCompanyNames();
  const list = Object.keys(names || {});
  await writeCache("cache:symbols", list);
  return list;
}

// --- the poll ------------------------------------------------------------

async function poll() {
  const settings = await getSettings();
  let payload;
  try {
    payload = await api.getAlerts();
  } catch (err) {
    await reflectError(err);
    return;
  }

  const alerts = payload?.alerts ?? [];
  await setLocal({ authState: "ok", lastError: null, cachedAlerts: payload, lastPoll: Date.now() });
  await setBadge(badgeText(payload?.unread), BADGE_OK, "Stock Signals");

  const { seenKeys, seeded } = await getLocal({ seenKeys: [], seeded: false });

  // First run: record everything and notify nothing. Without this, installing the
  // extension fires a notification for all 100 rows already in the backlog.
  if (!seeded) {
    await setLocal({
      seenKeys: pushSeen([], alerts.map((a) => a.dedup_key)),
      seeded: true,
    });
    return;
  }

  const fresh = diffNew(seenKeys, alerts);
  await setLocal({ seenKeys: pushSeen(seenKeys, alerts.map((a) => a.dedup_key)) });

  if (!settings.notifyHighSeverity) return;
  // Mirrors the backend's own push gate (alerts.py only emails/SMSes high severity).
  const high = fresh.filter((a) => a.severity === "high" && !a.read);
  if (!high.length) return;

  const shown = high.slice(0, MAX_NOTIFICATIONS_PER_POLL);
  for (const a of shown) {
    await createNotification(`alert:${a.dedup_key}`, {
      type: "basic",
      iconUrl: ext.runtime.getURL("icons/icon-128.png"),
      title: `${a.ticker} — ${a.title}`,
      message: a.message || "",
    });
  }
  const rest = high.length - shown.length;
  if (rest > 0) {
    await createNotification(`summary:${Date.now()}`, {
      type: "basic",
      iconUrl: ext.runtime.getURL("icons/icon-128.png"),
      title: `+${rest} more high-severity alert${rest === 1 ? "" : "s"}`,
      message: "Open the dashboard to review them.",
    });
  }
}

async function ensureAlarm() {
  const { pollMinutes } = await getSettings();
  const mins = Math.min(15, Math.max(1, Number(pollMinutes) || DEFAULTS.pollMinutes));
  await ext.alarms.clear(ALARM);
  await ext.alarms.create(ALARM, { periodInMinutes: mins, delayInMinutes: 0.1 });
}

// --- message broker ------------------------------------------------------

async function handle(msg) {
  switch (msg?.type) {
    case MSG.SCORES: {
      const rows = await boomScores();
      const want = new Set((msg.tickers || []).map((t) => String(t).toUpperCase()));
      const scores = {};
      for (const row of rows) {
        if (want.has(row.ticker)) scores[row.ticker] = row;
      }
      return { scores };
    }
    case MSG.SYMBOLS: {
      try {
        return { symbols: await symbols() };
      } catch {
        // Signed out or unreachable: the overlay degrades to cashtag-only rather
        // than disappearing.
        return { symbols: null };
      }
    }
    case MSG.ANALYZE:
      return api.analyze(msg.ticker);
    case MSG.ADD_WATCH:
      return api.addWatch(msg.ticker);
    case MSG.OPEN_TICKER: {
      const base = normalizeBase((await getSettings()).apiBase);
      if (base) await openTab(stockUrl(base, msg.ticker));
      return { opened: Boolean(base) };
    }
    case MSG.SITE_ENABLED:
      return { enabled: isSiteEnabled(await getSettings(), msg.host) };
    case MSG.SNAPSHOT: {
      const [quotes, boom, alerts] = await Promise.allSettled([
        api.getQuotes(),
        boomScores(),
        api.getAlerts(),
      ]);
      const state = await getLocal({ authState: "unknown", lastError: null });
      // A 401 on any of the three means the same thing; surface it once.
      const denied = [quotes, boom, alerts].find(
        (r) => r.status === "rejected" && (r.reason?.status === 401 || r.reason?.status === PERMISSION_DENIED),
      );
      if (denied) await reflectError(denied.reason);
      return {
        quotes: quotes.status === "fulfilled" ? quotes.value : null,
        boom: boom.status === "fulfilled" ? boom.value : null,
        alerts: alerts.status === "fulfilled" ? alerts.value : null,
        authState: denied ? (denied.reason.status === 401 ? "signed_out" : "no_permission") : state.authState,
        error:
          quotes.status === "rejected"
            ? quotes.reason?.message
            : alerts.status === "rejected"
              ? alerts.reason?.message
              : null,
      };
    }
    case MSG.MARK_READ: {
      const res = await api.markAlertsRead(msg.payload ?? { all: true });
      await setBadge(badgeText(res?.unread), BADGE_OK, "Stock Signals");
      await setLocal({ cachedAlerts: res });
      return res;
    }
    case MSG.POLL_NOW:
      await poll();
      return { polled: true };
    default:
      throw new ApiError(`unknown message: ${msg?.type}`, 0);
  }
}

// --- listeners (all registered synchronously) ----------------------------

ext.runtime.onInstalled.addListener(() => {
  ensureAlarm();
});

ext.runtime.onStartup?.addListener(() => {
  ensureAlarm();
});

ext.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) poll();
});

ext.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  handle(msg).then(
    (data) => sendResponse(ok(data)),
    (err) => sendResponse(fail(err)),
  );
  return true; // keep the channel open for the async reply
});

ext.notifications.onClicked.addListener(async (id) => {
  const base = normalizeBase((await getSettings()).apiBase);
  if (!base) return;
  const ticker = id.startsWith("alert:") ? tickerFromKey(id.slice("alert:".length)) : null;
  await openTab(ticker ? stockUrl(base, ticker) : base);
  try {
    await ext.notifications.clear(id);
  } catch {
    /* already dismissed */
  }
});

ext.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync") return;
  if (changes.apiBase) {
    // A new backend invalidates every cache and the seen-key history, and the
    // first poll against it must re-seed silently.
    setLocal({
      "cache:boom": null,
      "cache:symbols": null,
      seenKeys: [],
      seeded: false,
      authState: "unknown",
    }).then(() => poll());
  }
  if (changes.pollMinutes) ensureAlarm();
});
