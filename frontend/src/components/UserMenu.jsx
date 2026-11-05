import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import Popover from "./Popover";
import Tooltip from "./Tooltip";
import { initialsFor, gradientFor } from "../lib/avatar";
import { isUpdateAvailable, useUpdateStatus } from "../hooks/useUpdateStatus";
import styles from "./UserMenu.module.css";

/**
 * User identity chip + dropdown, replacing the bare email + logout icon.
 * Avatar initials on a deterministic gradient, the email local part beside it
 * (≥ 960px), and a keyboard-operable menu (Settings / Info / Log out) with an
 * Admin badge for the admin account.
 *
 * `phoneItems` ({id, label, icon, hint, run, disabled}) are shown only on a
 * phone (≤640px), where the top bar has no room for its own buttons.
 */
export default function UserMenu({ user, onLogout, onNavigate, phoneItems = [] }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const menuRef = useRef(null);
  const email = user?.email || "";
  const local = email.includes("@") ? email.split("@")[0] : email;
  // Info / Guide carries the Updates section; a dot here (and on the avatar,
  // while the menu is closed) says GitHub has something newer.
  const { data: updateData } = useUpdateStatus();
  const updateAvailable = isUpdateAvailable(updateData);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      // Portaled to <body>, so it is not inside wrapRef — check it separately.
      if (menuRef.current?.contains(e.target)) return;
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!user) return null;

  const go = (view) => { setOpen(false); onNavigate?.(view); };

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <Tooltip side="bottom" disabled={open}
               content={updateAvailable ? `Signed in as ${email} · update available` : `Signed in as ${email}`}>
        <button
          type="button"
          className={styles.trigger}
          onClick={() => setOpen((o) => !o)}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={`Account menu (${email})`}
        >
          <span className={styles.avatar} style={{ background: gradientFor(email) }} aria-hidden="true">
            {initialsFor(email)}
          </span>
          {updateAvailable && <span className={styles.triggerDot} aria-hidden="true" />}
          <span className={styles.local}>{local}</span>
        </button>
      </Tooltip>

      <Popover open={open} anchorRef={wrapRef} contentRef={menuRef} className={styles.menu} role="menu">
            <div className={styles.identity}>
              <span className={styles.avatarLg} style={{ background: gradientFor(email) }} aria-hidden="true">
                {initialsFor(email)}
              </span>
              <div className={styles.identityText}>
                <span className={styles.fullEmail}>{email}</span>
                {user.is_admin && <span className={styles.adminBadge}>Admin</span>}
              </div>
            </div>

            <div className={styles.divider} />

            {phoneItems.length > 0 && (
              <div className={styles.phoneOnly}>
                {phoneItems.map((it) => (
                  <button key={it.id} type="button" role="menuitem" className={styles.item}
                          disabled={it.disabled} onClick={() => { setOpen(false); it.run(); }}>
                    <Icon name={it.icon} size={15} /> {it.label}
                    {it.hint && <span className={styles.itemHint}>{it.hint}</span>}
                  </button>
                ))}
                <div className={styles.divider} />
              </div>
            )}

            <button type="button" role="menuitem" className={styles.item} onClick={() => go("settings")}>
              <Icon name="settings" size={15} /> Settings
            </button>
            <button type="button" role="menuitem" className={styles.item} onClick={() => go("info")}>
              <Icon name="info" size={15} /> Info / Guide
              {updateAvailable && (
                <Tooltip content="An app update is available — see Info → Updates" side="right">
                  <span className={styles.updateDot} role="img" aria-label="update available" />
                </Tooltip>
              )}
            </button>
            {/* Admin-only: exposes the DB path, tracebacks and machine stats.
                The route is gated server-side too — this is just the UI half. */}
            {user.is_admin && (
              <button type="button" role="menuitem" className={styles.item} onClick={() => go("server")}>
                <Icon name="layers" size={15} /> Server
              </button>
            )}

            <div className={styles.divider} />

            <button type="button" role="menuitem" className={`${styles.item} ${styles.logout}`} onClick={() => { setOpen(false); onLogout?.(); }}>
              <Icon name="arrowRight" size={15} /> Log out
            </button>
      </Popover>
    </div>
  );
}
