import { convictionTier, activeChips } from "../../lib/boom.js";

const LIMIT = 5;

export default function BoomPanel({ rows, loading, onOpen }) {
  // /api/boom-scores already arrives sorted by score DESC.
  const top = (rows ?? []).slice(0, LIMIT);

  return (
    <section className="panel">
      <h2>Top Boom Scores</h2>
      {loading && !top.length ? (
        <div className="list">
          <div className="skel" />
          <div className="skel" />
        </div>
      ) : !top.length ? (
        <div className="empty">No scores yet — they compute on the next refresh.</div>
      ) : (
        <div className="list">
          {top.map((row) => {
            const tier = convictionTier(row.score);
            const chips = activeChips(row, 2);
            return (
              <div
                key={row.ticker}
                className="item click"
                onClick={() => onOpen(row.ticker)}
                title={tier.label}
              >
                <span className={`score t-${tier.tone}`}>{row.score}</span>
                <span className="tk">{row.ticker}</span>
                <span className="grow truncate faint" style={{ fontSize: 11 }}>
                  {chips.map((c) => c.label).join(" · ")}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
