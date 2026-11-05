import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { animate } from "animejs";
import Icon from "./Icon";
import { applyUpdate, getHealth, getUpdateStatus } from "../api";
import { formatRelativeTime } from "../lib/format";
import { prefersReducedMotion } from "../lib/motionConfig";
import {
  isUpdateAvailable,
  refreshUpdateStatus,
  setUpdateStatus,
  useUpdateStatus,
} from "../hooks/useUpdateStatus";
import styles from "./UpdatesSection.module.css";
import Tooltip from "./Tooltip";

const PROGRESS_POLL_MS = 2000;
const RELOAD_DELAY_S = 5;
// How long to wait for a restarted server before saying so plainly.
const RECONNECT_GIVE_UP_MS = 4 * 60 * 1000;

const DEFAULT_STEPS = [
  { id: "pull", label: "Pull from GitHub", state: "pending", detail: "" },
  { id: "deps", label: "Install Python packages", state: "pending", detail: "" },
  { id: "frontend", label: "Rebuild the web app", state: "pending", detail: "" },
  { id: "restart", label: "Restart", state: "pending", detail: "" },
];

const sameCommit = (a, b) => Boolean(a && b && (a.startsWith(b) || b.startsWith(a)));

/**
 * The Info page's "Updates" section: which build is running, what GitHub has
 * that this checkout doesn't, and (admins only) a one-click update with live
 * progress. Everything shown comes from git via /api/update/status — if git
 * or GitHub can't be reached, the section says so instead of guessing.
 */
export default function UpdatesSection() {
  const { data, error, loading } = useUpdateStatus();
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [applyError, setApplyError] = useState(null);
  // null | "running" | "restarting" | "reconnected" | "timeout"
  const [phase, setPhase] = useState(null);
  const [countdown, setCountdown] = useState(null);
  const restartSince = useRef(0);

  const check = data?.check;
  const current = data?.current;
  const apply = data?.apply;
  const isAdmin = Boolean(data?.viewer_is_admin);
  const available = isUpdateAvailable(data);
  const target = apply?.new_commit || apply?.target_commit || check?.remote_commit;

  const checkNow = async () => {
    setChecking(true);
    setCheckError(null);
    try {
      await refreshUpdateStatus(true);
    } catch (e) {
      setCheckError(e.message);
    } finally {
      setChecking(false);
    }
  };

  const startUpdate = async () => {
    setConfirming(false);
    setApplyError(null);
    try {
      const body = await applyUpdate();
      setUpdateStatus(body);
      setPhase("running");
    } catch (e) {
      setApplyError(e.message);
      refreshUpdateStatus().catch(() => {});
    }
  };

  // Resume following an update that is already underway (another tab
  // started it, or this page was reloaded mid-update).
  const flow = phase ?? (apply?.state === "running" ? "running" : null);

  const reconnect = () => {
    setCountdown(RELOAD_DELAY_S);
    setPhase("reconnected");
  };

  // Follow progress. While the server restarts, /api/update/status can't
  // answer, so switch to /api/health until it reports the new commit.
  const tick = useCallback(async () => {
    if (flow === "running") {
      try {
        const body = await getUpdateStatus();
        setUpdateStatus(body);
        const st = body.apply?.state;
        if (st === "done") {
          const h = await getHealth().catch(() => null);
          const want = body.apply?.new_commit;
          if (sameCommit(h?.commit, want)) reconnect();
          else setPhase("finished");
        } else if (st && st !== "running") {
          setPhase("finished");
        }
      } catch {
        restartSince.current = Date.now();
        setPhase("restarting");
      }
    } else if (flow === "restarting") {
      try {
        const h = await getHealth();
        if (sameCommit(h?.commit, target)) {
          reconnect();
          refreshUpdateStatus().catch(() => {});
          return;
        }
        // Back up but on the old commit: the updater may have rolled back.
        const body = await getUpdateStatus().catch(() => null);
        if (body) {
          setUpdateStatus(body);
          const st = body.apply?.state;
          if (st && st !== "running") setPhase("finished");
        }
      } catch {
        if (Date.now() - restartSince.current > RECONNECT_GIVE_UP_MS) setPhase("timeout");
      }
    }
  }, [flow, target]);

  useEffect(() => {
    if (flow !== "running" && flow !== "restarting") return undefined;
    const id = setInterval(tick, PROGRESS_POLL_MS);
    return () => clearInterval(id);
  }, [flow, tick]);

  // Once the new build answers, count down and reload into it.
  useEffect(() => {
    if (flow !== "reconnected") return undefined;
    const id = setInterval(() => setCountdown((n) => Math.max(0, (n ?? 1) - 1)), 1000);
    return () => clearInterval(id);
  }, [flow]);
  useEffect(() => {
    if (flow === "reconnected" && countdown === 0) window.location.reload();
  }, [flow, countdown]);

  const following = flow === "running" || flow === "restarting";
  const showProgress = Boolean(flow) || Boolean(apply?.state && apply.state !== "unknown");
  const steps = apply?.steps?.length ? apply.steps : DEFAULT_STEPS;

  let disabledReason = null;
  if (!isAdmin) disabledReason = "Only an admin can install updates.";
  else if (following || apply?.state === "running") disabledReason = "An update is already running.";
  else if (check && !check.can_apply) disabledReason = check.blocked_reason;

  return (
    <div className={styles.wrap}>
      <div className={styles.summary}>
        <dl className={styles.facts}>
          <div>
            <dt>Running</dt>
            <dd>
              v{current?.version || "?"}
              {current?.commit
                ? <code className={styles.sha}>{current.commit}</code>
                : <span className={styles.muted}> (commit unknown — git unavailable)</span>}
            </dd>
          </div>
          <div>
            <dt>Branch</dt>
            <dd>{check ? (check.branch || "detached HEAD") : "—"}</dd>
          </div>
          <div>
            <dt>Last checked</dt>
            <dd>{check?.checked_at ? formatRelativeTime(check.checked_at) : "not yet"}</dd>
          </div>
        </dl>
        <Tooltip content={following
          ? "An update is being installed — checking is paused until it finishes"
          : checking ? "Checking GitHub for new commits…" : "Compare this install with the latest version on GitHub"}>
          <button
            type="button"
            className={styles.secondary}
            onClick={checkNow}
            disabled={checking || following}
          >
            <Icon name="refresh" size={14} /> {checking ? "Checking GitHub…" : "Check now"}
          </button>
        </Tooltip>
      </div>

      {(checkError || (error && !data)) && (
        <p className={styles.error} role="alert">Could not load update status: {checkError || error}</p>
      )}

      {!data && loading && <p className={styles.muted}>Checking for updates…</p>}

      {check && !check.ok && (
        <p className={styles.error} role="status">{check.error}</p>
      )}

      {check?.ok && !available && (
        <p className={styles.upToDate}>
          <span className={styles.okDot} aria-hidden="true" />
          Up to date with <code className={styles.sha}>{check.remote}</code>
          {check.ahead > 0 && ` (this checkout has ${check.ahead} local commit${check.ahead === 1 ? "" : "s"} not on GitHub)`}.
        </p>
      )}

      {available && (
        <div className={styles.available}>
          <p className={styles.availableHead}>
            <span className={styles.newDot} aria-hidden="true" />
            <strong>{check.behind} new commit{check.behind === 1 ? "" : "s"}</strong> on{" "}
            <code className={styles.sha}>{check.remote}</code>
            {check.latest_version && check.latest_version !== current?.version && (
              <> · version <strong>{check.latest_version}</strong></>
            )}
          </p>
          <ol className={styles.commits}>
            {check.commits.map((c) => (
              <li key={c.sha} className={styles.commit}>
                <code className={styles.sha}>{c.sha}</code>
                <span className={styles.subject}>{c.subject}</span>
                <span className={styles.meta}>{c.author} · {formatRelativeTime(c.date)}</span>
              </li>
            ))}
          </ol>
          {check.commits_truncated && (
            <p className={styles.muted}>…and {check.behind - check.commits.length} older.</p>
          )}
          {check.dirty_count > 0 && (
            <p className={styles.muted}>
              Locally modified: {check.dirty_files.join(", ")}
              {check.dirty_count > check.dirty_files.length && ` and ${check.dirty_count - check.dirty_files.length} more`}
            </p>
          )}

          {confirming ? (
            <div className={styles.confirm} role="alertdialog" aria-label="Confirm update">
              <span className={styles.confirmText}>
                Pull {check.behind} commit{check.behind === 1 ? "" : "s"}, reinstall packages, rebuild
                the web app and restart the server? The dashboard is unavailable for a minute or
                two. If any step fails it rolls back to <code className={styles.sha}>{check.current_commit}</code>.
              </span>
              <button type="button" className={styles.secondary} onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button type="button" className={styles.primary} onClick={startUpdate} autoFocus>
                Update now
              </button>
            </div>
          ) : (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.primary}
                disabled={Boolean(disabledReason)}
                aria-describedby={disabledReason ? "update-blocked" : undefined}
                onClick={() => setConfirming(true)}
              >
                <Icon name="arrowRight" size={14} /> Update now
              </button>
              {disabledReason && (
                <span id="update-blocked" className={styles.muted}>{disabledReason}</span>
              )}
            </div>
          )}
          {applyError && <p className={styles.error} role="alert">{applyError}</p>}
        </div>
      )}

      {showProgress && (
        <Progress
          steps={steps}
          apply={apply}
          phase={flow}
          countdown={countdown}
          target={target}
          runningCommit={current?.commit}
        />
      )}
    </div>
  );
}

function Progress({ steps, apply, phase, countdown, target, runningCommit }) {
  const listRef = useRef(null);
  const fillRef = useRef(null);
  const pulses = useRef(new Map());
  const seen = useRef(new Map());

  const doneCount = steps.filter((s) => s.state === "done" || s.state === "skipped").length;
  const fraction = steps.length ? doneCount / steps.length : 0;

  // Animate step transitions (animejs, gated by the app-wide reduced-motion
  // policy): a pop when a step completes or fails, a soft pulse while one runs,
  // and the rail filling as steps finish.
  useLayoutEffect(() => {
    const reduced = prefersReducedMotion();
    if (fillRef.current) {
      if (reduced) fillRef.current.style.height = `${fraction * 100}%`;
      else animate(fillRef.current, { height: `${fraction * 100}%`, duration: 500, ease: "outExpo" });
    }
    const root = listRef.current;
    if (!root) return;
    for (const s of steps) {
      const dot = root.querySelector(`[data-step="${s.id}"] [data-dot]`);
      if (!dot) continue;
      const prev = seen.current.get(s.id);
      seen.current.set(s.id, s.state);
      const running = pulses.current.get(s.id);
      if (s.state !== "running" && running) {
        running.cancel();
        dot.style.transform = "";
        pulses.current.delete(s.id);
      }
      if (reduced || prev === s.state) continue;
      if (s.state === "running" && !running) {
        pulses.current.set(s.id, animate(dot, {
          scale: [1, 1.25], duration: 700, ease: "inOutSine", loop: true, alternate: true,
        }));
      } else if (prev !== undefined && ["done", "failed", "skipped"].includes(s.state)) {
        animate(dot, { scale: [0.4, 1], duration: 450, ease: "outBack" });
      }
    }
  }, [fraction, steps]);

  useEffect(() => () => {
    pulses.current.forEach((a) => a.cancel());
    pulses.current.clear();
  }, []);

  const state = apply?.state;
  let banner = null;
  if (phase === "restarting") {
    banner = { tone: "info", text: "Restarting the server — waiting for it to come back…" };
  } else if (phase === "reconnected") {
    banner = {
      tone: "ok",
      text: `Updated to ${target}. Reloading in ${countdown ?? RELOAD_DELAY_S}s…`,
      reload: true,
    };
  } else if (phase === "timeout") {
    banner = {
      tone: "bad",
      text: "The server has not come back after 4 minutes. Check the service (windows\\service-control.ps1 -Status) and update.log.",
    };
  } else if (state === "done") {
    // Offer a reload only while this page is older than the update.
    banner = { tone: "ok", text: apply.message, reload: !sameCommit(runningCommit, apply.new_commit) || Boolean(phase) };
  } else if (state === "rolled_back") {
    banner = { tone: "warn", text: apply.message };
  } else if (state === "failed" || state === "unknown") {
    banner = { tone: "bad", text: apply.message };
  }

  return (
    <div className={styles.progress} aria-live="polite">
      <h4 className={styles.progressTitle}>
        {state === "running" || phase === "running" || phase === "restarting" ? "Updating…" : "Last update"}
        {apply?.started_at && (
          <span className={styles.meta}> · started {formatRelativeTime(apply.started_at)}</span>
        )}
      </h4>
      <div className={styles.stepper}>
        <div className={styles.rail} aria-hidden="true"><div ref={fillRef} className={styles.railFill} /></div>
        <ol ref={listRef} className={styles.steps}>
          {steps.map((s) => (
            <li key={s.id} data-step={s.id} data-state={s.state} className={styles.step}>
              <span data-dot className={styles.stepDot} aria-hidden="true" />
              <span className={styles.stepLabel}>{s.label}</span>
              <span className={styles.stepState}>{s.state}</span>
              {s.detail && <span className={styles.stepDetail}>{s.detail}</span>}
            </li>
          ))}
        </ol>
      </div>
      {banner && (
        <p className={styles.banner} data-tone={banner.tone} role={banner.tone === "bad" ? "alert" : "status"}>
          {banner.text}
          {banner.reload && (
            <button type="button" className={styles.secondary} onClick={() => window.location.reload()}>
              Reload now
            </button>
          )}
        </p>
      )}
    </div>
  );
}
