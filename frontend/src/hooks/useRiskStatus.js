import { useSyncExternalStore } from "react";
import { acknowledgeDrawdown, getRiskStatus, haltTrading, releaseTrading, saveRiskSettings } from "../api";

// The kill switch only trips on the evening equity snapshot (or a settings
// save), so a slow poll is enough to catch a trip made elsewhere.
const POLL_MS = 3 * 60 * 1000;

/*
 * One shared store: the app-wide banner and the Risk tab both read it, and it
 * should cost one request per poll, not one per consumer.
 */
let state = { data: null, error: null, loading: false };
const listeners = new Set();
let timer = null;

function emit(patch) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

export function refreshRiskStatus() {
  emit({ loading: true });
  return getRiskStatus()
    .then((data) => { emit({ data, error: null, loading: false }); return data; })
    .catch((e) => { emit({ error: e.message, loading: false }); throw e; });
}

/** Save the drawdown limit (null = off). Field errors reject as ApiError. */
export async function setMaxDrawdown(pct) {
  const data = await saveRiskSettings({ max_drawdown_pct: pct });
  emit({ data, error: null });
  return data;
}

/** What a risk-off regime does to new buys: "off" | "downgrade" | "pause". */
export async function setRegimeGate(gate) {
  const data = await saveRiskSettings({ regime_gate: gate });
  emit({ data, error: null });
  return data;
}

/** Change any of the order guardrails ({ max_order_notional, … }). */
export async function setGuardrails(limits) {
  const data = await saveRiskSettings(limits);
  emit({ data, error: null });
  return data;
}

/** The global kill switch. */
export async function haltAll(reason) {
  const data = await haltTrading(reason);
  emit({ data, error: null });
  return data;
}

/** Lift the kill switch with a fresh authenticator code. */
export async function releaseAll(code) {
  const data = await releaseTrading(code);
  emit({ data, error: null });
  return data;
}

/** Re-enable new entries after a trip. */
export async function acknowledgeTrip() {
  const data = await acknowledgeDrawdown();
  emit({ data, error: null });
  return data;
}

function subscribe(listener) {
  listeners.add(listener);
  if (listeners.size === 1) {
    refreshRiskStatus().catch(() => {});
    timer = setInterval(() => {
      if (!document.hidden) refreshRiskStatus().catch(() => {});
    }, POLL_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) { clearInterval(timer); timer = null; }
  };
}

const snapshot = () => state;

/** { data, error, loading } — data is the /api/risk/status payload. */
export function useRiskStatus() {
  return useSyncExternalStore(subscribe, snapshot);
}
