import { formatPrice, formatPct, toneOf } from "../../lib/format.js";
import ExtHoursBadge from "./ExtHoursBadge.jsx";

export default function QuotesPanel({ quotes, loading, onOpen }) {
  const rows = quotes?.quotes ?? [];

  return (
    <section className="panel">
      <h2>Watchlist</h2>
      {loading && !rows.length ? (
        <div className="list">
          <div className="skel" />
          <div className="skel" />
          <div className="skel" />
        </div>
      ) : !rows.length ? (
        <div className="empty">No tickers yet — add some in the dashboard.</div>
      ) : (
        <div className="list">
          {rows.map((q) => (
            <div
              key={q.ticker}
              className="item click"
              onClick={() => onOpen(q.ticker)}
              title={`Open ${q.ticker}`}
            >
              <span className="tk grow truncate">{q.ticker}</span>
              <ExtHoursBadge quote={q} />
              <span className="tabular mono">{formatPrice(q.price)}</span>
              <span className={`tabular mono ${toneOf(q.change_pct)}`}>
                {formatPct(q.change_pct)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
