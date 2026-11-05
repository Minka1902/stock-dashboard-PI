// Cross-browser WebExtension API shim.
//
// Firefox exposes `browser.*` with promise-returning methods; Chrome exposes
// `chrome.*`, which since MV3 also returns promises for most APIs — but not all.
// The exceptions are wrapped below so callers can always `await`.

export const ext = globalThis.browser ?? globalThis.chrome;

/** True in Firefox, where a few APIs differ (notably: no notification buttons). */
export const isGecko = Boolean(globalThis.browser?.runtime?.getBrowserInfo);

/**
 * chrome.notifications.create is callback-only in Chrome (it does not return a
 * promise even under MV3). Normalize it.
 */
export function createNotification(id, options) {
  return new Promise((res) => {
    try {
      const out = ext.notifications.create(id, options, (created) => {
        // Chrome sets runtime.lastError for e.g. a bad icon path; reading it
        // suppresses the "Unchecked runtime.lastError" console noise.
        void ext.runtime.lastError;
        res(created ?? id);
      });
      // Firefox returns a promise and ignores the callback.
      if (out && typeof out.then === "function") out.then(res, () => res(id));
    } catch {
      res(id);
    }
  });
}

/** Open a URL in a new tab. Safe to call from popup, options and background. */
export async function openTab(url) {
  try {
    await ext.tabs.create({ url });
  } catch {
    /* no tabs permission in this context — nothing sensible to do */
  }
}

/** Resolve the URL of the active tab, or null when it can't be read. */
export async function activeTabUrl() {
  try {
    const [tab] = await ext.tabs.query({ active: true, currentWindow: true });
    return tab?.url ?? null;
  } catch {
    return null;
  }
}
