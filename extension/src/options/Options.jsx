import { useEffect, useState } from "react";

import { ext } from "../lib/browser.js";
import { MSG } from "../lib/messages.js";
import { CONTENT_HOSTS } from "../lib/sites.js";
import {
  DEFAULTS,
  getSettings,
  setSettings,
  normalizeBase,
  originPattern,
} from "../lib/storage.js";

export default function Options() {
  const [settings, setLocalSettings] = useState(null);
  const [baseInput, setBaseInput] = useState("");
  const [granted, setGranted] = useState(false);
  const [test, setTest] = useState(null);

  useEffect(() => {
    getSettings().then((s) => {
      setLocalSettings(s);
      setBaseInput(s.apiBase);
      checkPermission(s.apiBase);
    });
  }, []);

  async function checkPermission(base) {
    const origin = normalizeBase(base);
    if (!origin) return setGranted(false);
    try {
      setGranted(await ext.permissions.contains({ origins: [originPattern(origin)] }));
    } catch {
      setGranted(false);
    }
  }

  async function save(patch) {
    await setSettings(patch);
    const s = await getSettings();
    setLocalSettings(s);
    return s;
  }

  async function saveBase() {
    const origin = normalizeBase(baseInput);
    if (!origin) {
      setTest({ ok: false, msg: "That isn't a valid http(s) address." });
      return;
    }
    setBaseInput(origin);
    await save({ apiBase: origin });
    await checkPermission(origin);
    setTest(null);
  }

  /**
   * permissions.request must run inside a user gesture, and Firefox only honours
   * it from a normal extension page — which is why this button lives here and the
   * popup only ever links to it.
   */
  async function grant() {
    const origin = normalizeBase(baseInput);
    if (!origin) return;
    try {
      const okGrant = await ext.permissions.request({ origins: [originPattern(origin)] });
      setGranted(okGrant);
      if (!okGrant) setTest({ ok: false, msg: "Permission was declined." });
    } catch (e) {
      setTest({ ok: false, msg: e?.message || "Could not request permission." });
    }
  }

  async function runTest() {
    setTest({ pending: true });
    const res = await ext.runtime.sendMessage({ type: MSG.SNAPSHOT });
    if (!res?.ok) {
      setTest({ ok: false, msg: res?.detail || "Unreachable." });
      return;
    }
    const { authState } = res.data;
    if (authState === "signed_out") {
      setTest({ ok: false, msg: "Backend reachable, but you're signed out. Sign in to the dashboard." });
    } else if (authState === "no_permission") {
      setTest({ ok: false, msg: "Access not granted for this address yet." });
    } else {
      setTest({ ok: true, msg: "Connected and signed in." });
    }
  }

  if (!settings) return <div className="empty">Loading…</div>;

  const siteOff = (host) => settings.sites?.[host] === false;

  return (
    <div>
      <h1 style={{ fontSize: 18, marginBottom: 4 }}>Stock Signals</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        Companion for your self-hosted Stock Signal Dashboard.
      </p>

      <section className="panel">
        <h2>Dashboard address</h2>
        <div className="row">
          <input
            className="field"
            value={baseInput}
            spellCheck={false}
            placeholder={DEFAULTS.apiBase}
            onChange={(e) => setBaseInput(e.target.value)}
          />
          <button className="btn" onClick={saveBase}>
            Save
          </button>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          {granted ? (
            <span className="note good">Access granted</span>
          ) : (
            <button className="btn primary" onClick={grant}>
              Grant access to this address
            </button>
          )}
          <button className="btn" onClick={runTest} disabled={!granted}>
            Test connection
          </button>
        </div>
        {test && !test.pending && (
          <div className={`note ${test.ok ? "good" : "bad"}`} style={{ marginTop: 8 }}>
            {test.msg}
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Alerts</h2>
        <label className="switch">
          <input
            type="checkbox"
            checked={settings.notifyHighSeverity}
            onChange={(e) => save({ notifyHighSeverity: e.target.checked })}
          />
          <span>Desktop notification for high-severity alerts</span>
        </label>
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted">Check every</span>
          <select
            className="field"
            style={{ width: 120 }}
            value={settings.pollMinutes}
            onChange={(e) => save({ pollMinutes: Number(e.target.value) })}
          >
            {[1, 2, 3, 5, 10, 15].map((m) => (
              <option key={m} value={m}>
                {m} min
              </option>
            ))}
          </select>
        </div>
        <p className="faint" style={{ fontSize: 11 }}>
          The backend recomputes alerts every 3 minutes, so polling faster than that finds
          nothing new.
        </p>
      </section>

      <section className="panel">
        <h2>Ticker badges</h2>
        <label className="switch">
          <input
            type="checkbox"
            checked={settings.overlayEnabled}
            onChange={(e) => save({ overlayEnabled: e.target.checked })}
          />
          <span>Show Boom Score badges on supported sites</span>
        </label>
        <div className="list" style={{ marginTop: 8, opacity: settings.overlayEnabled ? 1 : 0.5 }}>
          {CONTENT_HOSTS.map((host) => (
            <label key={host} className="switch">
              <input
                type="checkbox"
                disabled={!settings.overlayEnabled}
                checked={!siteOff(host)}
                onChange={(e) => save({ sites: { ...settings.sites, [host]: e.target.checked } })}
              />
              <span className="mono">{host}</span>
            </label>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>Appearance</h2>
        <select
          className="field"
          style={{ width: 160 }}
          value={settings.theme}
          onChange={async (e) => {
            const s = await save({ theme: e.target.value });
            document.documentElement.dataset.theme = s.theme;
          }}
        >
          <option value="dark">Dark</option>
          <option value="light">Light</option>
        </select>
      </section>
    </div>
  );
}
