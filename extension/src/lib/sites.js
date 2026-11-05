// Canonical host keys for the per-site overlay toggle.
//
// A page's location.hostname is "www.reddit.com" or "old.reddit.com", but the
// user thinks in terms of "reddit.com" and that's what the options list shows.
// Everything that reads or writes the `sites` map must go through siteKey() so
// the popup switch, the options checkbox and the content script agree.

/** The sites the content script is declared on, as user-facing keys. */
export const CONTENT_HOSTS = [
  "finance.yahoo.com",
  "reddit.com",
  "x.com",
  "twitter.com",
  "stocktwits.com",
  "cnbc.com",
  "marketwatch.com",
  "seekingalpha.com",
  "bloomberg.com",
];

/**
 * Map any hostname onto its canonical key.
 * Falls back to the hostname itself so unlisted hosts are still toggleable.
 */
export function siteKey(hostname) {
  const h = String(hostname || "").toLowerCase().replace(/^www\./, "");
  const match = CONTENT_HOSTS.find((c) => h === c || h.endsWith(`.${c}`));
  return match || h;
}

/** Is the overlay allowed to run on this hostname? */
export function isSiteEnabled(settings, hostname) {
  if (!settings?.overlayEnabled) return false;
  return settings.sites?.[siteKey(hostname)] !== false;
}
