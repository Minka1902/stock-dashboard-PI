import { useCallback, useEffect, useState } from "react";
import * as api from "../api";

// status: "loading" | "anon" | "totp_setup" | "totp_required" | "authed"
export default function useAuth() {
  const [status, setStatus] = useState("loading");
  const [user, setUser] = useState(null);

  useEffect(() => {
    let alive = true;
    api.getMe()
      .then((u) => { if (alive) { setUser(u); setStatus("authed"); } })
      .catch(() => { if (alive) setStatus("anon"); });
    return () => { alive = false; };
  }, []);

  // Session expired mid-use (any non-auth call got a 401).
  useEffect(() => {
    const onExpired = () => {
      setUser(null);
      setStatus((s) => (s === "authed" ? "anon" : s));
    };
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  const login = useCallback(async (email, password) => {
    const r = await api.login(email, password);
    setStatus(r.status === "totp_setup_required" ? "totp_setup" : "totp_required");
    return r;
  }, []);

  const register = useCallback(async (email, password, inviteCode) => {
    const r = await api.register(email, password, inviteCode);
    setStatus("totp_setup");
    return r;
  }, []);

  const verifyTotp = useCallback(async (code) => {
    const r = await api.totpVerify(code);
    setUser(r.user);
    setStatus("authed");
    return r;
  }, []);

  // Returns {user, recovery_codes}; caller shows the codes before continuing,
  // then calls finishSetup() to enter the app.
  const enableTotp = useCallback(async (code) => {
    const r = await api.totpEnable(code);
    setUser(r.user);
    return r;
  }, []);

  const finishSetup = useCallback(() => setStatus("authed"), []);

  const useRecovery = useCallback(async (code) => {
    const r = await api.useRecoveryCode(code);
    setUser(r.user);
    setStatus("authed");
    return r;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
      setStatus("anon");
    }
  }, []);

  const cancel = useCallback(() => setStatus("anon"), []);

  // Persist that this account has seen the guided tour (never auto-tour again,
  // on any device). Non-fatal if it fails — the tour just won't be suppressed.
  const markOnboarded = useCallback(async () => {
    try {
      const u = await api.markOnboarded();
      setUser(u);
    } catch { /* non-fatal */ }
  }, []);

  return {
    status, user,
    login, register, verifyTotp, enableTotp, finishSetup, useRecovery, logout, cancel,
    markOnboarded,
  };
}
