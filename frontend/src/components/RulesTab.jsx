import { useEffect, useState } from "react";
import BacktestResult from "./BacktestResult";
import Button from "./Button";
import ConfirmDialog from "./ConfirmDialog";
import Field from "./Field";
import RuleBuilder from "./RuleBuilder";
import { backtestRule, deleteRule, getRuleSchema, getSavedRules, saveRule } from "../api";
import { blankRule } from "../lib/ruleSpec";
import styles from "./RulesTab.module.css";

/**
 * The Rules tab of the Trading Bot page: build a rule from price and volume
 * conditions, backtest it on a ticker's full history (with the in-sample /
 * out-of-sample split and the variants-tested count), and save it. Saved
 * rules run daily as signals on your watched and held tickers and can drive
 * a "Custom rule" bot.
 */
export default function RulesTab() {
  const [schema, setSchema] = useState(null);
  const [saved, setSaved] = useState([]);
  const [spec, setSpec] = useState(blankRule);
  const [editingId, setEditingId] = useState(null);
  const [active, setActive] = useState(true);
  const [ticker, setTicker] = useState("SPY");
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(null);
  const [deleting, setDeleting] = useState(null);

  useEffect(() => {
    getRuleSchema().then(setSchema).catch((e) => setMessage(e.message));
    getSavedRules().then((d) => setSaved(d.rules)).catch(() => {});
  }, []);

  async function act(kind, fn) {
    setBusy(kind);
    setErrors({});
    setMessage(null);
    try {
      await fn();
    } catch (e) {
      setErrors(e.fields || {});
      setMessage(e.message);
    } finally {
      setBusy(null);
    }
  }

  const runBacktest = () => act("test", async () => setResult(await backtestRule(spec, ticker)));

  const save = () => act("save", async () => {
    const r = await saveRule(spec, active, editingId);
    setEditingId(r.id);
    setSaved((await getSavedRules()).rules);
    setMessage(`Saved as ${r.model_version}.`);
  });

  function edit(r) {
    setSpec(r.spec);
    setEditingId(r.id);
    setActive(r.active);
    setResult(null);
    setErrors({});
    setMessage(null);
  }

  function startNew() {
    setSpec(blankRule());
    setEditingId(null);
    setActive(true);
    setResult(null);
  }

  async function remove() {
    await deleteRule(deleting.id);
    setSaved((await getSavedRules()).rules);
    if (editingId === deleting.id) startNew();
  }

  if (!schema) return <p className={styles.placeholder}>{message || "Loading the rule builder…"}</p>;

  return (
    <div className={styles.root}>
      <section className={styles.section} aria-labelledby="saved-rules">
        <div className={styles.head}>
          <h3 id="saved-rules" className={styles.title}>Saved rules</h3>
          <Button size="sm" variant="quiet" onClick={startNew}>+ New rule</Button>
        </div>
        {saved.length === 0 ? (
          <p className={styles.text}>None yet. Build one below, backtest it, then save it to get its signals daily.</p>
        ) : (
          <ul className={styles.list}>
            {saved.map((r) => (
              <li key={r.id} className={styles.row} data-current={r.id === editingId ? "yes" : "no"}>
                <button type="button" className={styles.name} onClick={() => edit(r)}>{r.name}</button>
                <span className={styles.faint}>{r.model_version}</span>
                <span className={styles.faint}>{r.variants_tested} variant{r.variants_tested === 1 ? "" : "s"} tested</span>
                <span className={styles.state} data-on={r.active ? "yes" : "no"}>{r.active ? "Signalling" : "Off"}</span>
                <Button size="sm" variant="quiet" onClick={() => setDeleting(r)}>Delete</Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.section} aria-labelledby="rule-build">
        <h3 id="rule-build" className={styles.title}>{editingId ? "Edit rule" : "New rule"}</h3>
        <RuleBuilder value={spec} onChange={setSpec} schema={schema} errors={errors} />
        <label className={styles.check}>
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Signal daily on my watched and held tickers (alerts, suggestions; usable by a Custom rule bot)
        </label>
        <div className={styles.actions}>
          <Field label="Backtest on" inline>
            {(a) => <input {...a} value={ticker} size={10} onChange={(e) => setTicker(e.target.value.toUpperCase().trim())} />}
          </Field>
          <Button onClick={runBacktest} disabled={busy != null || !ticker}>{busy === "test" ? "Testing…" : "Backtest"}</Button>
          <Button variant="primary" onClick={save} disabled={busy != null}>
            {busy === "save" ? "Saving…" : editingId ? "Save changes" : "Save rule"}
          </Button>
        </div>
        {message && <p className={Object.keys(errors).length || !message.startsWith("Saved") ? styles.error : styles.ok}
                       role="status">{message}</p>}
      </section>

      {result && (
        <section className={styles.section} aria-label="Backtest result">
          <BacktestResult result={result} />
        </section>
      )}

      <ConfirmDialog open={deleting != null} tone="negative" caption="Saved rule" title={`Delete "${deleting?.name}"?`}
                     confirmLabel="Delete rule" onConfirm={remove} onClose={() => setDeleting(null)}>
        <p>Its daily signals stop. A Custom rule bot trading it stops entering new positions.</p>
      </ConfirmDialog>
    </div>
  );
}
