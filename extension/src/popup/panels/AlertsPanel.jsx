import { formatRelativeTime } from "../../lib/format.js";

const LIMIT = 8;

export default function AlertsPanel({ payload, loading, onMarkRead, onOpen }) {
  const alerts = payload?.alerts ?? [];
  const unread = payload?.unread ?? 0;
  // Unread first, then newest — the popup is a triage surface.
  const shown = [...alerts]
    .sort((a, b) => Number(a.read) - Number(b.read))
    .slice(0, LIMIT);

  return (
    <section className="panel">
      <div className="row between" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>Alerts{unread > 0 ? ` (${unread})` : ""}</h2>
        {unread > 0 && (
          <button className="btn link" onClick={() => onMarkRead({ all: true })}>
            Mark all read
          </button>
        )}
      </div>

      {loading && !shown.length ? (
        <div className="list">
          <div className="skel" />
          <div className="skel" />
        </div>
      ) : !shown.length ? (
        <div className="empty">Nothing new.</div>
      ) : (
        <div className="list">
          {shown.map((a) => (
            <div key={a.dedup_key} className={`item ${a.read ? "" : "unread"}`}>
              <span className={`sev ${a.severity}`} />
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="row" style={{ gap: 6 }}>
                  <button
                    className="btn link tk"
                    style={{ padding: 0 }}
                    onClick={() => onOpen(a.ticker)}
                  >
                    {a.ticker}
                  </button>
                  <span className="truncate">{a.title}</span>
                </div>
                <div className="faint truncate" style={{ fontSize: 11 }}>
                  {a.message}
                </div>
              </div>
              <div className="faint" style={{ fontSize: 10, whiteSpace: "nowrap" }}>
                {formatRelativeTime(a.created_at)}
              </div>
              {!a.read && (
                <button
                  className="btn link"
                  title="Mark read"
                  onClick={() => onMarkRead({ keys: [a.dedup_key] })}
                >
                  ✓
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
