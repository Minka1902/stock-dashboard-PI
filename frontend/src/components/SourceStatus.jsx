import { sourceState, SOURCE_META } from "../lib/sources";
import Tooltip from "./Tooltip";
import styles from "./SourceStatus.module.css";

// A compact strip flagging only the data sources that failed to respond. When every
// source is healthy this renders nothing — the full directory lives in Settings →
// Data sources. Never invents data: a red chip means the source recorded an error.
export default function SourceStatus({ sources, onOpenDetails }) {
  const failed = (sources || []).filter((s) => sourceState(s.status) === "error");
  if (failed.length === 0) return null;

  return (
    <div className={styles.row}>
      <span className={styles.label}>Not responding</span>
      {failed.map((s) => {
        const name = SOURCE_META[s.source]?.label || s.source;
        return (
          <Tooltip key={s.source} content={(
            <>
              <strong>{name} did not respond</strong>
              <p>{s.status}</p>
              <p>Click to see the full error on the Info page.</p>
            </>
          )}>
            <button
              type="button"
              className={styles.chip}
              data-state="error"
              onClick={onOpenDetails}
            >
              <span className={styles.dot} />
              <span className={styles.name}>{name}</span>
              <span className={styles.meta}>{s.status}</span>
            </button>
          </Tooltip>
        );
      })}
    </div>
  );
}
