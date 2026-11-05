import { useState } from "react";
import Button from "./Button";
import ExitRuleDialog from "./ExitRuleDialog";
import { useExitRules } from "../hooks/useExitRules";
import { openTickerTab } from "../lib/nav";
import { formatMoney } from "../lib/format";
import styles from "./ExitRulesTable.module.css";

const KIND_LABEL = { stop: "stop", take_profit: "target", trailing: "trail" };

function statusOf(item) {
  const r = item.rule;
  if (!r) return { tone: "none", text: "No rule" };
  if (!r.active) return { tone: "paused", text: "Paused" };
  if (r.fired.length) return { tone: "fired", text: `Alerted: ${r.fired.map((k) => KIND_LABEL[k] || k).join(", ")}` };
  return { tone: "watching", text: "Watching" };
}

/**
 * Every holding in one book with its exit rule: the levels in force, whether
 * each has alerted, and the plan's suggested stop where there's no stop yet.
 * Rows open ExitRuleDialog. Props: account ("manual" | "paper"), title,
 * emptyText (shown when the book holds nothing).
 */
export default function ExitRulesTable({ account = "manual", title = "Exit rules",
  emptyText = "No holdings in this book yet — add one on the Portfolio page." }) {
  const { items, loading, error, reload } = useExitRules(account);
  const [editing, setEditing] = useState(null);

  const held = items.filter((i) => i.held);
  const orphans = items.filter((i) => !i.held && i.rule);

  return (
    <section className={styles.root} aria-labelledby={`rules-${account}`}>
      <header className={styles.head}>
        <h3 id={`rules-${account}`} className={styles.title}>{title}</h3>
        <span className={styles.sub}>Stops, take-profits and trailing stops. They alert; they never sell.</span>
      </header>
      {error && <p className={styles.error} role="alert">{error}</p>}
      {loading ? (
        <p className={styles.empty}>Loading rules…</p>
      ) : held.length === 0 ? (
        <p className={styles.empty}>{emptyText}</p>
      ) : (
        <div className={styles.wrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col" className={styles.num}>Price</th>
                <th scope="col" className={styles.num}>Avg cost</th>
                <th scope="col" className={styles.num}>Stop</th>
                <th scope="col" className={styles.num}>Target</th>
                <th scope="col" className={styles.num}>Trail</th>
                <th scope="col">Status</th>
                <th scope="col" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {held.map((i) => {
                const st = statusOf(i);
                const m = (v) => (v == null ? "—" : formatMoney(v, i.currency));
                return (
                  <tr key={i.ticker}>
                    <td data-col="ticker">
                      <button type="button" className={styles.symbol} onClick={() => openTickerTab(i.ticker)}>
                        {i.ticker}
                      </button>
                    </td>
                    <td data-col="price" className={styles.num}>{m(i.price)}</td>
                    <td data-col="cost" className={styles.num}>{m(i.avg_cost)}</td>
                    <td data-col="stop" className={styles.num}>
                      {i.levels?.stop != null ? m(i.levels.stop) : i.suggested ? (
                        <span className={styles.suggested} title={i.suggested.reason}>
                          suggested {m(i.suggested.stop_price)}
                        </span>
                      ) : "—"}
                    </td>
                    <td data-col="target" className={styles.num}>{m(i.levels?.take_profit)}</td>
                    <td data-col="trail" className={styles.num}>
                      {i.rule?.trailing_pct != null ? `${i.rule.trailing_pct}% → ${m(i.levels?.trailing)}` : "—"}
                    </td>
                    <td data-col="status"><span className={styles.status} data-tone={st.tone}>{st.text}</span></td>
                    <td data-col="actions">
                      <Button size="sm" variant={i.rule ? "quiet" : "ghost"} onClick={() => setEditing(i)}>
                        {i.rule ? "Edit" : "Add rule"}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {orphans.length > 0 && (
        <p className={styles.empty}>
          Rules kept for tickers you no longer hold (inactive until you buy again): {orphans.map((o) => o.ticker).join(", ")}
        </p>
      )}
      {editing && (
        <ExitRuleDialog ticker={editing.ticker} account={account} item={editing}
                        onSaved={reload} onClose={() => setEditing(null)} />
      )}
    </section>
  );
}
