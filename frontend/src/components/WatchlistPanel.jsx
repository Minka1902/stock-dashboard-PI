import { useCallback, useState } from "react";
import { AnimatePresence, Reorder, useDragControls } from "motion/react";
import Icon from "./Icon";
import ExtHoursBadge from "./ExtHoursBadge";
import MenuButton, { MenuDivider, MenuItem, MenuLabel } from "./MenuButton";
import Sparkline from "./Sparkline";
import SparkRange from "./SparkRange";
import TickerLabel from "./TickerLabel";
import { useWatchlists } from "../hooks/useWatchlists";
import { useLocalOrder } from "../hooks/useLocalOrder";
import { useSparklines } from "../hooks/useSparklines";
import { openTickerTab } from "../lib/nav";
import { prefersReducedMotion, staggerItem } from "../lib/motionConfig";
import { currencyForSymbol, formatPrice, formatRelativeTime } from "../lib/format";
import styles from "./WatchlistPanel.module.css";
import Tooltip from "./Tooltip";

function changeTone(pct) {
  if (pct == null) return "flat";
  return pct >= 0 ? "pos" : "neg";
}

const itemId = (w) => w.ticker;

/**
 * One reorderable row. useDragControls has to live in a component per row —
 * hooks can't be called inside the map in the parent.
 */
function WatchRow({ item, position, total, onMove, children }) {
  const controls = useDragControls();
  const [dragging, setDragging] = useState(false);
  return (
    <Reorder.Item
      value={item}
      className={styles.item}
      data-dragging={dragging ? "true" : undefined}
      dragListener={false}
      dragControls={controls}
      onDragStart={() => setDragging(true)}
      onDragEnd={() => setDragging(false)}
      variants={staggerItem}
      exit={prefersReducedMotion() ? { opacity: 0 } : { opacity: 0, x: -12 }}
      layout={!prefersReducedMotion()}
    >
      <DragHandle
        controls={controls}
        ticker={item.ticker}
        onMove={onMove}
        position={position}
        total={total}
      />
      {children}
    </Reorder.Item>
  );
}

/**
 * A drag handle that also works from the keyboard.
 *
 * Reorder.Item is pointer-only, so on its own it would make reordering
 * mouse-exclusive. dragListener={false} + dragControls confines dragging to
 * this handle, which also keeps the row's inline note editor and kebab menu
 * clickable instead of swallowing every press as a drag.
 */
const MARKET_STATE_TIP = {
  LIVE: "Regular session open — the price is live.",
  REGULAR: "Regular session open — the price is live.",
  CLOSED: "Market closed — showing the last traded price.",
};

function DragHandle({ controls, ticker, onMove, position, total }) {
  return (
    <Tooltip content="Drag to reorder — or focus it and use the arrow keys" shortcut={[["↑"], ["↓"]]}>
    <button
      type="button"
      className={styles.handle}
      aria-label={`Reorder ${ticker}. Position ${position} of ${total}. Use arrow up and down to move.`}
      onPointerDown={(e) => {
        e.preventDefault(); // don't start a text selection while dragging
        // preventDefault on pointerdown also suppresses the browser's default
        // focus step, which would silently break the keyboard path below.
        e.currentTarget.focus();
        controls.start(e);
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowUp") { e.preventDefault(); onMove(-1); }
        if (e.key === "ArrowDown") { e.preventDefault(); onMove(1); }
      }}
    >
      <Icon name="grip" size={14} />
    </button>
    </Tooltip>
  );
}

export default function WatchlistPanel({ quotes = {}, marketStatus = null }) {
  const {
    lists, activeId, items, selectList,
    addTicker, removeTicker, moveTicker, editNote, createList, renameList, deleteList,
  } = useWatchlists();

  const [ticker, setTicker] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);
  const [range, setRange] = useState("1m");

  // Manual ordering, remembered locally per list. Server-side ordering would
  // need a schema column; local is enough while this is a single-device
  // preference, and it keeps the watchlist API untouched.
  const { ordered, isCustom, setOrderedItems, moveBy, reset: resetOrder } = useLocalOrder(
    activeId == null ? null : `watchlistOrder:v1:${activeId}`, items, itemId);
  const { series: sparks } = useSparklines(ordered.map((w) => w.ticker), range);

  // Announce keyboard moves — a visual reshuffle tells a screen reader nothing.
  const [liveMessage, setLiveMessage] = useState("");
  const handleMove = useCallback((ticker, delta) => {
    const next = moveBy(ticker, delta);
    if (!next) return;
    setLiveMessage(`${ticker} moved to position ${next.indexOf(ticker) + 1} of ${next.length}`);
  }, [moveBy]);

  // List tab editing state.
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [renamingId, setRenamingId] = useState(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  // Per-row note editing: the ticker being edited, plus its draft.
  const [noteTicker, setNoteTicker] = useState(null);
  const [noteDraft, setNoteDraft] = useState("");

  async function submit(e) {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    setPending(true);
    try {
      await addTicker(t, note.trim());
      setTicker("");
      setNote("");
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  }

  const commitCreate = () => {
    const n = newName.trim();
    setCreating(false);
    if (n) createList(n).catch(() => {});
  };
  const commitRename = () => {
    const n = renameDraft.trim();
    const id = renamingId;
    setRenamingId(null);
    if (n && id != null) renameList(id, n).catch(() => {});
  };

  const commitNote = () => {
    const t = noteTicker;
    const draft = noteDraft.trim();
    setNoteTicker(null);
    if (t == null) return;
    const current = items.find((w) => w.ticker === t)?.note ?? "";
    if (draft !== current) editNote(t, draft).catch((err) => setError(err.message));
  };

  const startNoteEdit = (w) => { setNoteDraft(w.note || ""); setNoteTicker(w.ticker); };

  return (
    <section className={styles.panel} id="watchlist">
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Watchlists</h2>
          <p className={styles.subtitle}>Group the tickers you want to keep an eye on</p>
        </div>
        <span className={styles.headerTools}>
          {isCustom && (
            <Tooltip content="Forget your manual order and go back to newest-added first">
              <button
                type="button"
                className={styles.resetOrder}
                onClick={() => { resetOrder(); setLiveMessage("Order reset to newest first"); }}
              >
                <Icon name="refresh" size={12} /> Reset order
              </button>
            </Tooltip>
          )}
          {items.length > 0 && <SparkRange value={range} onChange={setRange} />}
        </span>
      </header>

      {/* list selector */}
      <div className={styles.tabs} role="tablist" aria-label="Watchlists">
        {lists.map((l) => (
          renamingId === l.id ? (
            <input
              key={l.id}
              className={styles.tabInput}
              value={renameDraft}
              autoFocus
              maxLength={60}
              onChange={(e) => setRenameDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setRenamingId(null); }}
              onBlur={commitRename}
            />
          ) : (
            <span key={l.id} className={styles.tabWrap} data-active={l.id === activeId ? "yes" : "no"}>
              <button
                className={styles.tab}
                role="tab"
                aria-selected={l.id === activeId}
                onClick={() => selectList(l.id)}
              >
                {l.name}
              </button>
              {l.id === activeId && (
                <MenuButton label={`Actions for ${l.name}`} className={styles.tabMenu}>
                  {(close) => (
                    <>
                      <MenuItem onSelect={() => { close(); setRenameDraft(l.name); setRenamingId(l.id); }}>
                        <Icon name="edit" size={14} /> Rename
                      </MenuItem>
                      <MenuDivider />
                      <MenuItem
                        tone="danger"
                        disabled={lists.length <= 1}
                        onSelect={() => { close(); setConfirmDeleteId(l.id); }}
                      >
                        <Icon name="trash" size={14} />
                        {lists.length <= 1 ? "Can't delete your only list" : "Delete list"}
                      </MenuItem>
                    </>
                  )}
                </MenuButton>
              )}
            </span>
          )
        ))}
        {creating ? (
          <input
            className={styles.tabInput}
            value={newName}
            autoFocus
            maxLength={60}
            placeholder="List name"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") commitCreate(); if (e.key === "Escape") setCreating(false); }}
            onBlur={commitCreate}
          />
        ) : (
          <button className={styles.newTab} onClick={() => { setNewName(""); setCreating(true); }}>
            + New list
          </button>
        )}
      </div>

      {confirmDeleteId != null && (() => {
        const doomed = lists.find((l) => l.id === confirmDeleteId);
        const count = doomed?.id === activeId ? items.length : null;
        return (
          <div className={styles.confirm} role="alertdialog" aria-label="Confirm delete">
            <span className={styles.confirmText}>
              Delete <strong>{doomed?.name}</strong>
              {count ? ` and its ${count} ticker${count === 1 ? "" : "s"}` : " and everything on it"}?
              This can't be undone.
            </span>
            <button className={styles.confirmCancel} onClick={() => setConfirmDeleteId(null)}>
              Cancel
            </button>
            <button
              className={styles.confirmGo}
              onClick={() => {
                const id = confirmDeleteId;
                setConfirmDeleteId(null);
                deleteList(id).catch((err) => setError(err.message));
              }}
            >
              Delete list
            </button>
          </div>
        );
      })()}

      <form className={styles.form} onSubmit={submit} data-tour="add-form">
        <input
          className={styles.ticker}
          placeholder="Ticker (e.g. LMT)"
          value={ticker}
          maxLength={12}
          onChange={(e) => setTicker(e.target.value)}
        />
        <input
          className={styles.note}
          placeholder="Note (optional)"
          value={note}
          maxLength={120}
          onChange={(e) => setNote(e.target.value)}
        />
        <button className={styles.add} disabled={pending || !ticker.trim()}>
          {pending ? "Adding…" : "Add"}
        </button>
      </form>
      {error && <p className={styles.error}>{error}</p>}

      {items.length === 0 ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="star" size={24} /></span>
          <p className={styles.emptyTitle}>This watchlist is empty</p>
          <p className={styles.emptyText}>Add a ticker above to start tracking it.</p>
        </div>
      ) : (
        <Reorder.Group
          as="ul"
          axis="y"
          className={styles.list}
          values={ordered}
          onReorder={setOrderedItems}
        >
          <AnimatePresence initial={false}>
          {ordered.map((w, index) => {
            const q = quotes[w.ticker];
            return (
            <WatchRow
              key={w.ticker}
              item={w}
              position={index + 1}
              total={ordered.length}
              onMove={(delta) => handleMove(w.ticker, delta)}
            >
              <Tooltip content={`Open ${w.ticker} analysis in a new tab`}>
                <button
                  className={styles.symbolBtn}
                  onClick={() => openTickerTab(w.ticker)}
                >
                  <TickerLabel ticker={w.ticker} className={styles.symbol} />
                </button>
              </Tooltip>
              <span className={styles.price}>
                {/* In the listing's own currency: ₪ for a TASE symbol. */}
                {q && q.price != null ? formatPrice(q.price, q.currency || currencyForSymbol(w.ticker) || "USD") : "—"}
              </span>
              <span className={styles.change} data-tone={changeTone(q?.change_pct)}>
                {q && q.change_pct != null
                  ? `${q.change_pct >= 0 ? "+" : ""}${q.change_pct.toFixed(2)}%`
                  : "—"}
              </span>
              <ExtHoursBadge quote={q} />
              <Tooltip content={MARKET_STATE_TIP[marketStatus || q?.market_state]}>
                <span className={styles.state} data-state={(marketStatus || q?.market_state) ?? "none"}>
                  {(() => {
                    const eff = marketStatus || q?.market_state;
                    return eff && eff !== "PRE" && eff !== "POST" ? eff : "";
                  })()}
                </span>
              </Tooltip>
              <Sparkline
                closes={sparks[w.ticker]?.closes}
                changePct={sparks[w.ticker]?.change_pct}
                error={sparks[w.ticker]?.error}
                loading={!sparks[w.ticker]}
                range={range}
              />
              {noteTicker === w.ticker ? (
                <input
                  className={styles.noteInput}
                  value={noteDraft}
                  autoFocus
                  maxLength={120}
                  placeholder="Note"
                  aria-label={`Note for ${w.ticker}`}
                  onChange={(e) => setNoteDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitNote();
                    if (e.key === "Escape") setNoteTicker(null);
                  }}
                  onBlur={commitNote}
                />
              ) : (
                <Tooltip content={w.note ? (
                  <><strong>Note — click to edit</strong><p>{w.note}</p></>
                ) : `Add a note for ${w.ticker}`}>
                  <button
                    className={styles.itemNote}
                    onClick={() => startNoteEdit(w)}
                    aria-label={w.note ? `Edit note for ${w.ticker}: ${w.note}` : `Add a note for ${w.ticker}`}
                  >
                    {w.note || "—"}
                  </button>
                </Tooltip>
              )}
              <span className={styles.added}>added {formatRelativeTime(w.added_at)}</span>
              <MenuButton label={`Actions for ${w.ticker}`}>
                {(close) => (
                  <>
                    <MenuItem onSelect={() => { close(); openTickerTab(w.ticker); }}>
                      <Icon name="trending" size={14} /> Open analysis
                    </MenuItem>
                    <MenuItem onSelect={() => { close(); startNoteEdit(w); }}>
                      <Icon name="note" size={14} /> {w.note ? "Edit note" : "Add note"}
                    </MenuItem>

                    {lists.length > 1 && (
                      <>
                        <MenuLabel>Move to</MenuLabel>
                        {lists.filter((l) => l.id !== activeId).map((l) => (
                          <MenuItem
                            key={l.id}
                            onSelect={() => {
                              close();
                              moveTicker(w.ticker, l.id).catch((err) => setError(err.message));
                            }}
                          >
                            <Icon name="move" size={14} /> {l.name}
                          </MenuItem>
                        ))}
                      </>
                    )}

                    <MenuDivider />
                    <MenuLabel>Reorder</MenuLabel>
                    <MenuItem
                      disabled={index === 0}
                      onSelect={() => { close(); handleMove(w.ticker, -1); }}
                    >
                      <Icon name="arrowUp" size={14} /> Move up
                    </MenuItem>
                    <MenuItem
                      disabled={index === ordered.length - 1}
                      onSelect={() => { close(); handleMove(w.ticker, 1); }}
                    >
                      <Icon name="arrowDown" size={14} /> Move down
                    </MenuItem>

                    <MenuDivider />
                    <MenuItem tone="danger" onSelect={() => { close(); removeTicker(w.ticker); }}>
                      <Icon name="trash" size={14} /> Remove from list
                    </MenuItem>
                  </>
                )}
              </MenuButton>
            </WatchRow>
            );
          })}
          </AnimatePresence>
        </Reorder.Group>
      )}
      <p aria-live="polite" className={styles.srOnly}>{liveMessage}</p>
    </section>
  );
}
