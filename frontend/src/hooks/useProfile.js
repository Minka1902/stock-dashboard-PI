import { useCallback, useEffect, useState } from "react";
import { getProfile, saveProfile } from "../api";

const EMPTY = {
  email: "", phone: "", email_enabled: false, sms_enabled: false,
  account_size: null, risk_pct: 1.0, base_currency: "USD",
};

function fromServer(p) {
  return {
    email: p.email || "",
    phone: p.phone || "",
    email_enabled: !!p.email_enabled,
    sms_enabled: !!p.sms_enabled,
    account_size: p.account_size ?? null,
    risk_pct: p.risk_pct ?? 1.0,
    // Currency portfolio totals and account_size are expressed in.
    base_currency: p.base_currency || "USD",
  };
}

/**
 * Server-backed notification profile (email/phone/enable flags). Unlike
 * useSettings (localStorage display prefs), this persists to the backend so the
 * scheduler can deliver the daily digest. Secrets (SMTP/Twilio) never touch the
 * client — only contact info and enable flags.
 */
export function useProfile() {
  const [profile, setProfile] = useState(EMPTY);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    getProfile()
      .then((p) => {
        if (!alive) return;
        setProfile(fromServer(p));
      })
      .catch(() => {})
      .finally(() => alive && setLoaded(true));
    return () => { alive = false; };
  }, []);

  // Local edit (no network) — used while the user types.
  const update = useCallback((patch) => {
    setProfile((p) => ({ ...p, ...patch }));
  }, []);

  // Persist the current profile to the backend; returns the saved profile.
  const save = useCallback(async (next) => {
    const saved = await saveProfile(next);
    setProfile(fromServer(saved));
    return saved;
  }, []);

  return { profile, loaded, update, save };
}
