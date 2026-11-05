import { useState } from "react";
import Field from "./Field";
import HarvestList from "./HarvestList";
import RebalancePlanner from "./RebalancePlanner";
import SaleCheck from "./SaleCheck";
import Segmented from "./Segmented";
import TaxLotsTable from "./TaxLotsTable";
import { useLots } from "../hooks/useLots";
import { formatMoney } from "../lib/format";
import styles from "./LotsTab.module.css";

const ACCOUNTS = [
  { value: "manual", label: "Manual", title: "Your hand-tracked portfolio" },
  { value: "paper", label: "Paper", title: "The simulated paper account" },
];

/**
 * The Lots & rebalance tab of the Trading Bot page: an account's tax lots,
 * realized gains this year, tax-loss candidates, a sale check, and the plan
 * back to its target weights. Not tax advice.
 */
export default function LotsTab() {
  const [account, setAccount] = useState("manual");
  const [minLoss, setMinLoss] = useState(100);
  const [preset, setPreset] = useState(null);
  const { data, error, reload } = useLots(account, minLoss);

  const currencyOf = (t) => data?.positions.find((p) => p.ticker === t)?.currency || "USD";

  return (
    <div className={styles.root}>
      <div className={styles.bar}>
        <Segmented options={ACCOUNTS} value={account} onChange={(v) => { setAccount(v); setPreset(null); }} ariaLabel="Account" />
        {data && (
          <span className={styles.faint}>
            Realized in {data.realized.year}:{" "}
            {Object.keys(data.realized.by_currency).length === 0 ? "nothing yet" : Object.entries(data.realized.by_currency)
              .map(([c, g]) => `short term ${formatMoney(g.short, c)}, long term ${formatMoney(g.long, c)}`).join(" · ")}
            {data.realized.sales > 0 && ` (${data.realized.sales} lot sales${data.realized.approximate ? ", some approximate" : ""})`}
          </span>
        )}
      </div>
      {error && <p className={styles.error}>{error}</p>}
      {data && (
        <>
          <section className={styles.section} aria-labelledby="lots-title">
            <h3 id="lots-title" className={styles.title}>Tax lots</h3>
            {data.approximate && (
              <p className={styles.faint}>≈ marks a lot an edit set: its shares' purchase dates aren't known, so its term is a guess.</p>
            )}
            <TaxLotsTable positions={data.positions} />
          </section>
          <section className={styles.section} aria-labelledby="harvest-title">
            <div className={styles.head}>
              <h3 id="harvest-title" className={styles.title}>Tax-loss candidates</h3>
              <Field label="Losses from" suffix="ccy" inline>
                {(a) => <input {...a} type="number" min="0" step="10" size={6} value={minLoss}
                               onChange={(e) => setMinLoss(Number(e.target.value) || 0)} />}
              </Field>
            </div>
            <p className={styles.faint}>Wash sales are checked across {data.wash_scope}.</p>
            <HarvestList candidates={data.harvest} currencyOf={currencyOf} minLoss={minLoss} onCheck={setPreset} />
          </section>
          <section className={styles.section} aria-labelledby="sale-title">
            <h3 id="sale-title" className={styles.title}>What would a sale realize?</h3>
            <SaleCheck key={`${account}-${preset?.ticker}-${preset?.qty}`} account={account} positions={data.positions} preset={preset} />
          </section>
          <p className={styles.faint}>{data.note}</p>
        </>
      )}
      <section className={styles.section} aria-labelledby="rebalance-title">
        <h3 id="rebalance-title" className={styles.title}>Rebalance to targets</h3>
        <RebalancePlanner account={account} onPlaced={reload} />
      </section>
    </div>
  );
}
