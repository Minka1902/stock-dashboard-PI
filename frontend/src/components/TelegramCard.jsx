import { useCallback, useEffect, useState } from "react";
import Button from "./Button";
import { getTelegram, linkTelegram, setTelegramEnabled, unlinkTelegram } from "../api";
import { formatDate, formatUntil } from "../lib/format";
import styles from "./TelegramCard.module.css";

const POLL_MS = 3000;

/**
 * Settings → Telegram: connect a private chat with a one-time code (10-minute
 * life), then get high-severity alerts and bot fills there and ask the bot
 * read-only questions (/status, /bots, /watchlist, /alerts). While a code is
 * showing it checks every few seconds whether the chat got linked.
 * Props: legend ("Telegram").
 */
export default function TelegramCard({ legend = "Telegram" }) {
  const [info, setInfo] = useState(null);
  const [issued, setIssued] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => getTelegram().then(setInfo).catch((e) => setError(e.message)), []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!issued || info?.linked) return undefined;
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [issued, info?.linked, load]);

  async function run(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const connect = () => run(async () => setIssued(await linkTelegram()));
  const toggle = (enabled) => run(async () => setInfo(await setTelegramEnabled(enabled)));
  const unlink = () => run(async () => { setInfo(await unlinkTelegram()); setIssued(null); });

  return (
    <fieldset className={styles.group}>
      <legend className={styles.legend}>{legend}</legend>
      <p className={styles.hint}>
        High-severity alerts (exit-rule hits, kill switches, recommendations turning buy or sell) and bot fills, sent to a private
        Telegram chat. The bot also answers /status, /bots, /watchlist and /alerts — read-only, it never trades.
      </p>
      {!info ? <p className={styles.muted}>{error || "Loading…"}</p> : !info.configured ? (
        <p className={styles.muted}>
          Not set up on this server: an admin sets STOCKS_TELEGRAM_BOT_TOKEN (a token from @BotFather) and restarts.
        </p>
      ) : info.linked ? (
        <div className={styles.row}>
          <span>
            Linked{info.username ? <> to <strong>@{info.username}</strong></> : null}
            {info.linked_at && <span className={styles.muted}> · since {formatDate(info.linked_at)}</span>}
          </span>
          <label className={styles.check}>
            <input type="checkbox" checked={info.enabled} disabled={busy} onChange={(e) => toggle(e.target.checked)} />
            Send me alerts and fills
          </label>
          <Button size="sm" variant="quiet" onClick={unlink} disabled={busy}>Unlink</Button>
        </div>
      ) : issued ? (
        <div className={styles.steps}>
          <p>
            Send this to {issued.bot_username ? <strong>@{issued.bot_username}</strong> : "the bot"} in a private
            chat, {formatUntil(issued.expires_at)}:
          </p>
          <code className={styles.code}>/start {issued.code}</code>
          <div className={styles.row}>
            {issued.deep_link && (
              <a className={styles.linkBtn} href={issued.deep_link} target="_blank" rel="noreferrer">Open Telegram</a>
            )}
            <Button size="sm" variant="quiet" onClick={connect} disabled={busy}>New code</Button>
            <span className={styles.muted} role="status">Waiting for your message…</span>
          </div>
        </div>
      ) : (
        <Button size="sm" variant="primary" onClick={connect} disabled={busy}>{busy ? "…" : "Connect Telegram"}</Button>
      )}
      {error && info && <p className={styles.err} role="alert">{error}</p>}
    </fieldset>
  );
}
