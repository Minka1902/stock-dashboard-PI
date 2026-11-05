import { useEffect, useRef, useState } from "react";
import { totpSetup, getOAuthProviders, getRegistrationMode, oauthStartUrl } from "../api";
import styles from "./AuthGate.module.css";

const PROVIDER_LABEL = { github: "GitHub", google: "Google", facebook: "Facebook" };

// Compact monochrome brand glyphs (currentColor).
const PROVIDER_ICON = {
  github: (
    <path d="M12 1.5a10.5 10.5 0 0 0-3.32 20.46c.52.1.71-.23.71-.5v-1.76c-2.9.63-3.52-1.4-3.52-1.4-.47-1.2-1.16-1.52-1.16-1.52-.95-.65.07-.64.07-.64 1.05.07 1.6 1.08 1.6 1.08.93 1.6 2.45 1.14 3.05.87.09-.68.36-1.14.66-1.4-2.32-.26-4.76-1.16-4.76-5.16 0-1.14.4-2.07 1.07-2.8-.1-.26-.46-1.32.1-2.75 0 0 .88-.28 2.88 1.07a10 10 0 0 1 5.24 0c2-1.35 2.87-1.07 2.87-1.07.57 1.43.21 2.49.1 2.75.67.73 1.07 1.66 1.07 2.8 0 4.01-2.45 4.9-4.78 5.16.38.32.71.95.71 1.92v2.85c0 .28.19.61.72.5A10.5 10.5 0 0 0 12 1.5Z" />
  ),
  google: (
    <path d="M21.35 11.1H12v2.98h5.35c-.23 1.4-1.6 4.1-5.35 4.1a5.98 5.98 0 0 1 0-11.96c1.87 0 3.13.8 3.85 1.48l2.62-2.53C16.9 3.6 14.66 2.6 12 2.6A9.4 9.4 0 1 0 12 21.4c5.42 0 9-3.8 9-9.16 0-.62-.07-1.09-.15-1.14Z" />
  ),
  facebook: (
    <path d="M22 12a10 10 0 1 0-11.56 9.88v-6.99H7.9V12h2.54V9.8c0-2.5 1.49-3.89 3.78-3.89 1.09 0 2.24.2 2.24.2v2.46h-1.26c-1.24 0-1.63.77-1.63 1.56V12h2.78l-.44 2.89h-2.34v6.99A10 10 0 0 0 22 12Z" />
  ),
};

function ProviderIcon({ provider }) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
      {PROVIDER_ICON[provider]}
    </svg>
  );
}

function Field({ label, ...props }) {
  return (
    <label className={styles.field}>
      <span className={styles.fieldLabel}>{label}</span>
      <input className={styles.input} {...props} />
    </label>
  );
}

function CodeInput({ value, onChange, autoFocus }) {
  return (
    <input
      className={`${styles.input} ${styles.code}`}
      value={value}
      onChange={(e) => onChange(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
      inputMode="numeric"
      autoComplete="one-time-code"
      placeholder="000000"
      aria-label="6-digit authenticator code"
      autoFocus={autoFocus}
    />
  );
}

function LoginRegister({ auth, busy, run }) {
  const [picked, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [providers, setProviders] = useState([]);
  // Assume open until told otherwise, so a failed probe never hides the tab
  // on a local install where signup is fine.
  const [signup, setSignup] = useState("open");

  useEffect(() => {
    getOAuthProviders().then((d) => setProviders(d.providers || [])).catch(() => {});
    getRegistrationMode().then((d) => setSignup(d.mode || "open")).catch(() => {});
  }, []);

  // When the server refuses signups there is nothing to show but a sign-in box.
  // Derived rather than an effect: if the mode probe lands while the register
  // tab is open, the form falls back to login on that same render.
  const tabs = signup === "closed" ? ["login"] : ["login", "register"];
  const mode = tabs.includes(picked) ? picked : "login";

  const submit = (e) => {
    e.preventDefault();
    if (mode === "register" && password !== confirm) {
      run(() => Promise.reject(new Error("passwords do not match")));
      return;
    }
    run(() => (mode === "login"
      ? auth.login(email, password)
      : auth.register(email, password, inviteCode)));
  };

  return (
    <form onSubmit={submit}>
      <div className={styles.tabs} role="tablist">
        {tabs.map((m) => (
          <button
            key={m} type="button" role="tab" aria-selected={mode === m}
            className={styles.tab} data-active={mode === m ? "yes" : "no"}
            onClick={() => setMode(m)}
          >
            {m === "login" ? "Sign in" : "Create account"}
          </button>
        ))}
      </div>
      <Field label="Email" type="email" value={email} required autoComplete="email"
             onChange={(e) => setEmail(e.target.value)} />
      <Field label="Password" type="password" value={password} required
             minLength={8} maxLength={128}
             autoComplete={mode === "login" ? "current-password" : "new-password"}
             onChange={(e) => setPassword(e.target.value)} />
      {mode === "register" && (
        <Field label="Confirm password" type="password" value={confirm} required
               autoComplete="new-password" onChange={(e) => setConfirm(e.target.value)} />
      )}
      {mode === "register" && signup === "invite" && (
        <Field label="Invite code" type="text" value={inviteCode} required
               autoComplete="off" onChange={(e) => setInviteCode(e.target.value)} />
      )}
      <button className={styles.primary} disabled={busy}>
        {mode === "login" ? "Continue" : "Create account"}
      </button>

      {providers.length > 0 && (
        <>
          <div className={styles.orRow}><span>or</span></div>
          <div className={styles.oauthBtns}>
            {providers.map((p) => (
              <a key={p} className={styles.oauthBtn} href={oauthStartUrl(p)}>
                <ProviderIcon provider={p} />
                Continue with {PROVIDER_LABEL[p] || p}
              </a>
            ))}
          </div>
          <p className={styles.hint}>
            Social login still requires two-factor authentication on the next step.
          </p>
        </>
      )}

      {mode === "register" && (
        <p className={styles.hint}>
          Two-factor authentication is required — have Google or Microsoft
          Authenticator ready on your phone.
        </p>
      )}
    </form>
  );
}

function TotpChallenge({ auth, busy, run }) {
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [recoveryCode, setRecoveryCode] = useState("");

  const submit = (e) => {
    e.preventDefault();
    run(() => (useRecovery ? auth.useRecovery(recoveryCode) : auth.verifyTotp(code)));
  };

  return (
    <form onSubmit={submit}>
      <h2 className={styles.step}>Two-factor check</h2>
      {useRecovery ? (
        <Field label="Recovery code" value={recoveryCode} placeholder="xxxx-xxxx-xxxx"
               required onChange={(e) => setRecoveryCode(e.target.value)} autoFocus />
      ) : (
        <>
          <p className={styles.hint}>Enter the 6-digit code from your authenticator app.</p>
          <CodeInput value={code} onChange={setCode} autoFocus />
        </>
      )}
      <button className={styles.primary} disabled={busy || (!useRecovery && code.length !== 6)}>
        Verify
      </button>
      <button type="button" className={styles.linkBtn}
              onClick={() => setUseRecovery((v) => !v)}>
        {useRecovery ? "Use authenticator code instead" : "Lost your device? Use a recovery code"}
      </button>
      <button type="button" className={styles.linkBtn} onClick={auth.cancel}>
        Back to sign in
      </button>
    </form>
  );
}

function TotpSetup({ auth, busy, run }) {
  const [setup, setSetup] = useState(null);
  const [error, setError] = useState("");
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState(null);

  useEffect(() => {
    let alive = true;
    totpSetup()
      .then((s) => { if (alive) setSetup(s); })
      .catch((e) => { if (alive) setError(e.message || "could not start 2FA setup"); });
    return () => { alive = false; };
  }, []);

  if (recoveryCodes) {
    return (
      <div>
        <h2 className={styles.step}>Save your recovery codes</h2>
        <p className={styles.hint}>
          Each code signs you in once if you lose your authenticator. They are
          shown only now — store them somewhere safe.
        </p>
        <ul className={styles.codes}>
          {recoveryCodes.map((c) => <li key={c}>{c}</li>)}
        </ul>
        <button className={styles.primary} onClick={auth.finishSetup}>
          I saved them — open the dashboard
        </button>
      </div>
    );
  }

  const submit = (e) => {
    e.preventDefault();
    run(async () => {
      const r = await auth.enableTotp(code);
      setRecoveryCodes(r.recovery_codes);
    });
  };

  return (
    <form onSubmit={submit}>
      <h2 className={styles.step}>Set up two-factor authentication</h2>
      {error && <p className={styles.error}>{error}</p>}
      {!setup && !error && <p className={styles.hint}>Preparing your QR code…</p>}
      {setup && (
        <>
          <p className={styles.hint}>
            Scan with Google or Microsoft Authenticator, then enter the 6-digit
            code it shows.
          </p>
          {/* PNG comes from our own backend (segno-generated), not user content. */}
          <div className={styles.qr}>
            <img src={setup.qr_png} width={220} height={220} alt="Two-factor setup QR code" />
          </div>
          <details className={styles.manual}>
            <summary>Can't scan? Enter the key manually</summary>
            <code className={styles.secret}>{setup.secret}</code>
          </details>
          <CodeInput value={code} onChange={setCode} />
          <button className={styles.primary} disabled={busy || code.length !== 6}>
            Activate
          </button>
        </>
      )}
      <button type="button" className={styles.linkBtn} onClick={auth.cancel}>
        Back to sign in
      </button>
    </form>
  );
}

export default function AuthGate({ auth, children }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const errRef = useRef(null);

  const run = (fn) => {
    setBusy(true);
    setError("");
    fn()
      .catch((e) => {
        setError(e.message || "something went wrong");
        errRef.current?.focus();
      })
      .finally(() => setBusy(false));
  };

  if (auth.status === "authed") return children;

  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>◆</span>
          <span className={styles.brandName}>SIGNAL</span>
          <span className={styles.brandSub}>terminal</span>
        </div>
        {auth.status === "loading" && <p className={styles.hint}>Checking session…</p>}
        {error && auth.status !== "loading" && (
          <p className={styles.error} tabIndex={-1} ref={errRef} role="alert">{error}</p>
        )}
        {auth.status === "anon" && <LoginRegister auth={auth} busy={busy} run={run} />}
        {auth.status === "totp_required" && <TotpChallenge auth={auth} busy={busy} run={run} />}
        {auth.status === "totp_setup" && <TotpSetup auth={auth} busy={busy} run={run} />}
        <p className={styles.foot}>Signals, not predictions — your data stays on your server.</p>
      </div>
    </div>
  );
}
