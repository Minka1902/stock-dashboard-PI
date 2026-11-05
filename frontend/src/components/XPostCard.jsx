import { useState } from "react";
import Icon from "./Icon";
import { openTickerTab } from "../lib/nav";
import { initialsFor, gradientFor } from "../lib/avatar";
import { formatRelativeTime } from "../lib/format";
import styles from "./XPostCard.module.css";
import Tooltip from "./Tooltip";

const CASHTAG_SPLIT_RE = /(\$[A-Za-z]{1,5})\b/g;
const CASHTAG_TEST_RE = /^\$[A-Za-z]{1,5}$/;

// Post text with clickable $TICKER chips.
function PostText({ text }) {
  const parts = text.split(CASHTAG_SPLIT_RE);
  return (
    <p className={styles.text}>
      {parts.map((part, i) => {
        if (CASHTAG_TEST_RE.test(part)) {
          const t = part.slice(1).toUpperCase();
          return (
            <Tooltip key={i} content={`Analyze ${t} in a new tab`}>
              <button
                type="button"
                className={styles.cashtag}
                onClick={() => openTickerTab(t)}
              >
                {part}
              </button>
            </Tooltip>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </p>
  );
}

// Best-effort real profile image via unavatar (no key). Falls back to the
// deterministic gradient-initials avatar when the image can't load (offline,
// unknown handle, blocked) so an avatar always renders.
function XAvatar({ account }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span
        className={styles.avatar}
        style={{ background: gradientFor(account) }}
        aria-hidden="true"
      >
        {initialsFor(account)}
      </span>
    );
  }
  return (
    <img
      className={styles.avatarImg}
      src={`https://unavatar.io/x/${account}`}
      alt=""
      loading="lazy"
      aria-hidden="true"
      onError={() => setFailed(true)}
    />
  );
}

/**
 * A single X (Twitter) post card: avatar, handle, timestamp, source link,
 * cashtag-linked text and detected-ticker chips. Shared by the X Watch panel,
 * the News "X" tab and the stock-analysis X pane. `compact` tightens it for
 * the narrower News/analysis columns.
 */
export default function XPostCard({ post, compact = false }) {
  return (
    <article className={`${styles.card} ${compact ? styles.compact : ""}`}>
      <XAvatar account={post.account} />
      <div className={styles.body}>
        <div className={styles.head}>
          <span className={styles.handle}>@{post.account}</span>
          {post.posted_at && (
            <span className={styles.time}>{formatRelativeTime(post.posted_at)}</span>
          )}
          {post.url && (
            <Tooltip content="Open the original post on X (new tab)">
              <a
                className={styles.link}
                href={post.url}
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Open original post"
              >
                <Icon name="arrowRight" size={13} />
              </a>
            </Tooltip>
          )}
        </div>
        <PostText text={post.text} />
        {post.tickers && (
          <div className={styles.tickerRow}>
            {post.tickers.split(",").filter(Boolean).map((t) => (
              <Tooltip key={t} content={`Analyze ${t} in a new tab`}>
                <button
                  type="button"
                  className={styles.tickerChip}
                  onClick={() => openTickerTab(t)}
                >
                  {t}
                </button>
              </Tooltip>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
