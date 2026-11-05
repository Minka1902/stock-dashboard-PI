import { Fragment, useState } from "react";
import ScrollHint from "./ScrollHint";
import { formatDate, formatMoney } from "../lib/format";
import styles from "./TaxLotsTable.module.css";

const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");

/**
 * Every position with its unrealized gain split short / long term, each
 * expandable to its lots (acquired, size, cost, gain, term, the day it turns
 * long term). An approximate lot — one an edit set — is marked. Props:
 * positions (GET /api/portfolio/lots), emptyText.
 */
export default function TaxLotsTable({ positions, emptyText = "No positions in this account." }) {
  const [open, setOpen] = useState({});

  if (!positions.length) return <p className={styles.empty}>{emptyText}</p>;
  return (
    <ScrollHint className={styles.wrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Ticker</th><th className={styles.num}>Shares</th><th className={styles.num}>Price</th>
            <th className={styles.num}>Short-term</th><th className={styles.num}>Long-term</th><th>Lots</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const m = (v) => (v == null ? "—" : formatMoney(v, p.currency));
            return (
              <Fragment key={p.ticker}>
                <tr>
                  <td className={styles.ticker}>{p.ticker}</td>
                  <td className={styles.num}>{+p.qty.toFixed(4)}</td>
                  <td className={styles.num} title={p.price_source || "no price"}>{m(p.price)}</td>
                  <td className={styles.num} data-tone={tone(p.unrealized.short)}>{m(p.unrealized.short)}</td>
                  <td className={styles.num} data-tone={tone(p.unrealized.long)}>{m(p.unrealized.long)}</td>
                  <td>
                    <button type="button" className={styles.toggle} aria-expanded={!!open[p.ticker]}
                            onClick={() => setOpen({ ...open, [p.ticker]: !open[p.ticker] })}>
                      {p.lots.length} lot{p.lots.length === 1 ? "" : "s"} {open[p.ticker] ? "▴" : "▾"}
                    </button>
                  </td>
                </tr>
                {open[p.ticker] && p.lots.map((l) => (
                  <tr key={l.id} className={styles.lot}>
                    <td>{l.id}{!l.exact && <span className={styles.approx} title="an edit set this position; when these shares were bought isn't known"> ≈</span>}</td>
                    <td className={styles.num}>{+l.qty.toFixed(4)}</td>
                    <td className={styles.num}>{m(l.per_share)}</td>
                    <td className={styles.num} colSpan={2} data-tone={tone(l.gain)}>
                      {m(l.gain)} <span className={styles.faint}>{l.term}</span>
                    </td>
                    <td className={styles.faint}>
                      bought {formatDate(l.acquired)}{l.term === "short" ? ` · long term after ${formatDate(l.long_term_from)}` : ""}
                    </td>
                  </tr>
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </ScrollHint>
  );
}
