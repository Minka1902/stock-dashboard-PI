import { useEffect, useRef, useState } from "react";
import Popover from "../Popover";
import Tooltip from "../Tooltip";
import {
  canCopyImage, canShareFile, copyImage, downloadBlob,
} from "../../lib/chartSnapshot";
import DrawIcon from "./DrawIcons";
import styles from "./ChartTools.module.css";

/**
 * "Snapshot" toolbar action. The chart is captured the moment the button is
 * pressed — exactly the range, studies and drawings on screen — and the menu
 * then offers what to do with that image: share (Web Share API, where the
 * browser can share files), copy to the clipboard, or download a PNG.
 */
export default function SnapshotMenu({ capture, onToast, disabled }) {
  const [snap, setSnap] = useState(null); // { blob, file, url, name }
  const [busy, setBusy] = useState(false);
  const btnRef = useRef(null);
  const menuRef = useRef(null);
  const open = !!snap;

  // Revoke the preview URL when the snapshot is replaced or dropped.
  useEffect(() => () => { if (snap?.url) URL.revokeObjectURL(snap.url); }, [snap]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (menuRef.current?.contains(e.target) || btnRef.current?.contains(e.target)) return;
      setSnap(null);
    };
    const onKey = (e) => { if (e.key === "Escape") { setSnap(null); btnRef.current?.focus(); } };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const take = async () => {
    if (open) { setSnap(null); return; }
    setBusy(true);
    try {
      const result = await capture();
      const file = new File([result.blob], result.name, { type: "image/png" });
      setSnap({ ...result, file, url: URL.createObjectURL(result.blob) });
    } catch (e) {
      onToast(`Snapshot failed: ${e.message || "could not capture the chart"}`, "error");
    } finally {
      setBusy(false);
    }
  };

  const shareable = snap ? canShareFile(snap.file) : false;
  const copyable = canCopyImage();

  const run = async (kind) => {
    if (!snap) return;
    try {
      if (kind === "share") {
        await navigator.share({ files: [snap.file], title: snap.title, text: snap.title });
        onToast("Shared the chart snapshot");
      } else if (kind === "copy") {
        await copyImage(snap.blob);
        onToast("Chart image copied to the clipboard");
      } else {
        downloadBlob(snap.blob, snap.name);
        onToast(`Downloaded ${snap.name}`);
      }
      setSnap(null);
    } catch (e) {
      if (e?.name === "AbortError") return; // user closed the share sheet
      onToast(`${kind === "copy" ? "Copy" : kind === "share" ? "Share" : "Download"} failed: ${e.message || e}`, "error");
    }
  };

  return (
    <>
      <Tooltip side="bottom" disabled={open || busy} content={disabled
        ? "Snapshot — available once the chart has data"
        : "Screenshot the chart as it is now — share, copy or download"}>
        <button ref={btnRef} type="button" className={styles.toolbarBtn} disabled={disabled || busy}
                aria-haspopup="menu" aria-expanded={open}
                onClick={take}>
          <DrawIcon name="camera" size={15} />
          <span>{busy ? "Capturing…" : "Snapshot"}</span>
        </button>
      </Tooltip>
      <Popover open={open} anchorRef={btnRef} contentRef={menuRef} className={styles.menu}
               role="menu" aria-label="Chart snapshot">
        {snap && (
          <>
            <img className={styles.snapPreview} src={snap.url} alt={`Snapshot of ${snap.title}`} />
            <div className={styles.menuActions}>
              {shareable && (
                <button type="button" role="menuitem" className={styles.menuItem} onClick={() => run("share")}>
                  <DrawIcon name="share" size={15} /> Share…
                </button>
              )}
              <Tooltip side="left" content={copyable
                ? "Copy the PNG to the clipboard"
                : "This browser can't put images on the clipboard — download instead"}>
                <button type="button" role="menuitem" className={styles.menuItem} disabled={!copyable}
                        onClick={() => run("copy")}>
                  <DrawIcon name="copy" size={15} /> Copy image
                </button>
              </Tooltip>
              <button type="button" role="menuitem" className={styles.menuItem} onClick={() => run("download")}>
                <DrawIcon name="download" size={15} /> Download PNG
              </button>
            </div>
            {!shareable && (
              <p className={styles.menuNote}>This browser can't share files directly — copy or download instead.</p>
            )}
          </>
        )}
      </Popover>
    </>
  );
}
