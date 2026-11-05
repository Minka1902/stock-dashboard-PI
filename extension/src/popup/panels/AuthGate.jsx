import { ext, openTab } from "../../lib/browser.js";

/**
 * Replaces the popup body when we can't read data.
 *
 * The two failure modes need different fixes, so they get different copy:
 * a missing host permission is fixed on the options page (the permission request
 * needs a user gesture there), a 401 is fixed by signing in to the dashboard.
 */
export default function AuthGate({ state, base, onRetry }) {
  const noPermission = state === "no_permission";

  return (
    <section className="panel">
      <h2>{noPermission ? "Access needed" : "Signed out"}</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        {noPermission ? (
          <>
            The extension needs your permission to talk to{" "}
            <span className="mono">{base || "your dashboard"}</span>.
          </>
        ) : (
          <>
            Your dashboard session has expired. Sign in and this popup will fill in on the next
            refresh.
          </>
        )}
      </p>
      <div className="row">
        {noPermission ? (
          <button className="btn primary" onClick={() => ext.runtime.openOptionsPage()}>
            Open settings
          </button>
        ) : (
          <button className="btn primary" onClick={() => base && openTab(base)} disabled={!base}>
            Sign in
          </button>
        )}
        <button className="btn" onClick={onRetry}>
          Retry
        </button>
      </div>
    </section>
  );
}
