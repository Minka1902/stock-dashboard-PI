import { useEffect, useState } from "react";

import { ext, openTab, activeTabUrl } from "../lib/browser.js";
import { getSettings, setSettings, normalizeBase, stockUrl } from "../lib/storage.js";
import { siteKey, isSiteEnabled } from "../lib/sites.js";
import { useSnapshot } from "./useSnapshot.js";
import QuotesPanel from "./panels/QuotesPanel.jsx";
import BoomPanel from "./panels/BoomPanel.jsx";
import AlertsPanel from "./panels/AlertsPanel.jsx";
import AuthGate from "./panels/AuthGate.jsx";

export default function Popup() {
  const { quotes, boom, alerts, authState, error, loading, reload, markRead } = useSnapshot();
  const [base, setBase] = useState(null);
  const [host, setHost] = useState(null);
  const [siteOn, setSiteOn] = useState(true);

  useEffect(() => {
    getSettings().then((s) => {
      setBase(normalizeBase(s.apiBase));
      activeTabUrl().then((url) => {
        if (!url) return;
        try {
          const h = siteKey(new URL(url).hostname);
          setHost(h);
          setSiteOn(isSiteEnabled(s, h));
        } catch {
          /* chrome:// and about: pages have no useful host */
        }
      });
    });
  }, []);

  async function toggleSite(next) {
    setSiteOn(next);
    const s = await getSettings();
    await setSettings({ sites: { ...s.sites, [host]: next } });
  }

  const blocked = authState === "signed_out" || authState === "no_permission";

  return (
    <div className="wrap">
      <header className="bar">
        <span className={`dot-status ${blocked || error ? "warn" : "ok"}`} />
        <span className="brand grow">Stock Signals</span>
        {quotes?.market_status && <span className="faint">{quotes.market_status}</span>}
        <button className="btn link" onClick={reload} title="Refresh now">
          Refresh
        </button>
        <button className="btn link" onClick={() => ext.runtime.openOptionsPage()} title="Settings">
          Settings
        </button>
      </header>

      {blocked ? (
        <AuthGate state={authState} base={base} onRetry={reload} />
      ) : (
        <>
          {error && <div className="panel"><div className="note bad">{error}</div></div>}
          <QuotesPanel
            quotes={quotes}
            loading={loading}
            onOpen={(t) => base && openTab(stockUrl(base, t))}
          />
          <BoomPanel
            rows={boom}
            loading={loading}
            onOpen={(t) => base && openTab(stockUrl(base, t))}
          />
          <AlertsPanel
            payload={alerts}
            loading={loading}
            onMarkRead={markRead}
            onOpen={(t) => base && openTab(stockUrl(base, t))}
          />
        </>
      )}

      <section className="panel">
        <div className="row between">
          <button className="btn" onClick={() => base && openTab(base)} disabled={!base}>
            Open dashboard
          </button>
          {host && (
            <label className="switch" title={`Show ticker badges on ${host}`}>
              <input
                type="checkbox"
                checked={siteOn}
                onChange={(e) => toggleSite(e.target.checked)}
              />
              <span className="muted truncate">Badges on {host}</span>
            </label>
          )}
        </div>
      </section>
    </div>
  );
}
