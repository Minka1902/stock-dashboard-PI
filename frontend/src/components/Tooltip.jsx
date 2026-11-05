import {
  cloneElement, isValidElement, useCallback, useEffect, useId, useLayoutEffect, useRef, useState,
} from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { prefersReducedMotion } from "../lib/motionConfig";
import { placeFloating } from "../lib/floating";
import styles from "./Tooltip.module.css";

/**
 * Accessible tooltip for a single trigger element.
 *
 *   <Tooltip content="Undo" shortcut={["Ctrl", "Z"]}>
 *     <button aria-label="Undo">…</button>
 *   </Tooltip>
 *
 * Behaviour (WAI-ARIA APG tooltip pattern + WCAG 1.4.13):
 *  - Mouse hover opens after `delay` (400ms). Once any tooltip has been open
 *    in the last WARM_WINDOW_MS, the next one opens instantly — scanning a
 *    toolbar should not cost 400ms per button. Only one is open at a time.
 *  - Keyboard focus opens immediately, but only for :focus-visible, so a
 *    mouse click does not pop a tooltip over the thing just clicked.
 *  - Pointer can move onto the tooltip without it closing (hoverable); it
 *    stays until the pointer leaves both, focus leaves, or Esc (persistent,
 *    dismissible). Esc is caught at window capture and stopped there, so it
 *    closes the tooltip only — not also the dialog or menu underneath. The
 *    listener only exists while a tooltip is open.
 *  - Touch: long-press shows it, a tap anywhere else hides it.
 *  - Pressing the trigger hides it until the pointer leaves.
 *  - The trigger gets `aria-describedby` pointing at the tooltip. While the
 *    tooltip is closed a hidden copy carries the same id, so screen readers
 *    get the description without having to wait for the popup. When the text
 *    would only repeat the trigger's own aria-label, no description is wired.
 *  - Portaled to <body> (same reason as Popover) and flipped/slid to stay in
 *    the viewport (lib/floating.js).
 *
 * The child must be a single DOM element (or a component that passes its
 * props and ref through to one); the native `title` on it is dropped so the
 * browser tooltip never doubles up.
 *
 * Disabled controls: a native `disabled` button fires no pointer events, so a
 * tooltip explaining *why* it is disabled would never open. When the child
 * has `disabled`, Tooltip wraps it in an inline-flex <span> that takes the
 * events instead (the button gets `pointer-events: none`). Pass
 * `wrapperClassName` if that span needs layout (e.g. `flex: 1`). Keyboard
 * users cannot focus a disabled button, so never make the tooltip the only
 * place a disabled reason is available when it matters.
 *
 * Tooltips are supplementary: essential information must also be visible or
 * reachable without hovering.
 *
 * @param content   tooltip body (string or node); falsy renders the child alone
 * @param shortcut  keyboard shortcut rendered as <kbd>: "Alt+T", ["Ctrl","Z"],
 *                  or alternatives [["Ctrl","Y"],["Ctrl","Shift","Z"]]
 * @param side      preferred side: top | bottom | left | right (flips to fit)
 * @param delay     hover delay in ms
 * @param disabled  suppress the tooltip entirely
 * @param truncate  only show when the trigger's text is actually clipped
 *                  (ellipsis); the content defaults to the trigger's text
 * @param openOnClick  a click toggles it open and pins it (InfoTip)
 */

const WARM_WINDOW_MS = 500;
const CLOSE_GRACE_MS = 120;
const LONG_PRESS_MS = 500;
const GAP = 8;

// Shared by every instance: which tooltip is open, and when the last closed.
// Only touched through the functions below, from handlers and effects.
const group = { owner: null, close: null, lastClosedAt: 0 };

/** Make `token` the one open tooltip, closing whichever was open before. */
function claimGroup(token, close) {
  if (group.owner && group.owner !== token) group.close?.();
  group.owner = token;
  group.close = close;
}

function releaseGroup(token, closed = true) {
  if (group.owner !== token) return;
  group.owner = null;
  group.close = null;
  if (closed) group.lastClosedAt = Date.now();
}

/** True while a tooltip is open or one closed moments ago: skip the delay. */
function groupIsWarm() {
  return group.owner != null || Date.now() - group.lastClosedAt < WARM_WINDOW_MS;
}

function assignRef(ref, node) {
  if (typeof ref === "function") ref(node);
  else if (ref && typeof ref === "object") ref.current = node;
}

/** Normalise the `shortcut` prop to a list of key combos. */
function toCombos(shortcut) {
  if (!shortcut) return null;
  if (typeof shortcut === "string") return [shortcut.split("+").map((k) => k.trim())];
  if (!Array.isArray(shortcut) || shortcut.length === 0) return null;
  return shortcut.every(Array.isArray) ? shortcut : [shortcut];
}

/** "Alt+T Control+Y" — the aria-keyshortcuts spelling. */
function ariaKeys(combos) {
  const NAMES = { Ctrl: "Control", Esc: "Escape", Del: "Delete", "⌘": "Meta", "↑": "ArrowUp", "↓": "ArrowDown" };
  const name = (k) => NAMES[k] || k;
  return combos.map((c) => c.map(name).join("+")).join(" ");
}

/** Keyboard shortcut chips — exported for anywhere else a shortcut is shown. */
export function Keys({ combos, className = "" }) {
  return (
    <span className={`${styles.keys} ${className}`}>
      {combos.map((combo, i) => (
        <span key={combo.join("+")} className={styles.combo}>
          {i > 0 && <span className={styles.or}>or</span>}
          {combo.map((k, j) => (
            <span key={k} className={styles.combo}>
              {j > 0 && <span className={styles.plus} aria-hidden="true">+</span>}
              <kbd className={styles.kbd}>{k}</kbd>
            </span>
          ))}
        </span>
      ))}
    </span>
  );
}

const stop = (e) => e.stopPropagation();

function isClipped(el) {
  if (!el) return false;
  return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
}

function matchesFocusVisible(el) {
  try {
    return el.matches(":focus-visible");
  } catch {
    return true;
  }
}

const ORIGIN = {
  top: (a) => `${a}px 100%`,
  bottom: (a) => `${a}px 0`,
  left: (a) => `100% ${a}px`,
  right: (a) => `0 ${a}px`,
};

export default function Tooltip({
  content,
  shortcut,
  side = "top",
  delay = 400,
  disabled = false,
  truncate = false,
  openOnClick = false,
  arrow = true,
  wrapperClassName = "",
  children,
}) {
  const id = useId();
  const [token] = useState(() => ({}));
  const [open, setOpen] = useState(false);
  const [present, setPresent] = useState(false);
  const [pos, setPos] = useState(null);
  const [clippedText, setClippedText] = useState(null);
  const triggerRef = useRef(null);
  const tipRef = useRef(null);
  const timers = useRef({ open: 0, close: 0, press: 0 });
  const mode = useRef(null); // how it was opened: hover | focus | touch | click
  const suppressed = useRef(false);

  const child = isValidElement(children) ? children : <span>{children}</span>;
  const childRef = child.props.ref;
  const combos = toCombos(shortcut);
  const body = truncate ? (content ?? clippedText) : content;
  const hasContent = !!body || !!combos;
  const shown = open && !disabled && hasContent;

  const clear = (key) => {
    clearTimeout(timers.current[key]);
    timers.current[key] = 0;
  };

  const hide = useCallback(() => {
    clearTimeout(timers.current.open);
    clearTimeout(timers.current.close);
    clearTimeout(timers.current.press);
    mode.current = null;
    setOpen(false);
    releaseGroup(token);
  }, [token]);

  const show = (how) => {
    if (disabled) return;
    if (truncate) {
      const el = triggerRef.current;
      if (!isClipped(el)) return;
      if (content == null) setClippedText(el.textContent);
    } else if (!hasContent) {
      return;
    }
    clear("open");
    clear("close");
    claimGroup(token, hide);
    mode.current = how;
    setOpen(true);
    setPresent(true);
  };

  const scheduleClose = () => {
    clear("close");
    timers.current.close = setTimeout(hide, CLOSE_GRACE_MS);
  };

  // Clean up timers (and group ownership) on unmount.
  useEffect(() => () => {
    const t = timers.current;
    clearTimeout(t.open);
    clearTimeout(t.close);
    clearTimeout(t.press);
    releaseGroup(token, false);
  }, [token]);

  // Position before paint, on every render while open (content can change
  // while it is showing, e.g. a toggle's on/off text). setPos bails out when
  // nothing moved, so this settles after one pass.
  useLayoutEffect(() => {
    if (!shown) return undefined;
    const place = () => {
      const t = triggerRef.current;
      const tip = tipRef.current;
      if (!t || !tip) return;
      const next = placeFloating(
        t.getBoundingClientRect(),
        { width: tip.offsetWidth, height: tip.offsetHeight },
        side,
        GAP,
      );
      setPos((p) => (p && p.top === next.top && p.left === next.left
        && p.side === next.side && p.arrow === next.arrow ? p : next));
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  });

  // Dismissal while open: Esc (captured first, and stopped so it does not
  // also close a parent dialog), and a press anywhere outside.
  useEffect(() => {
    if (!shown) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      suppressed.current = true;
      hide();
    };
    const onDown = (e) => {
      if (triggerRef.current?.contains(e.target) || tipRef.current?.contains(e.target)) return;
      hide();
    };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onDown, true);
    };
  }, [shown, hide]);

  const setTrigger = useCallback((node) => {
    triggerRef.current = node;
    assignRef(childRef, node);
  }, [childRef]);

  // With a disabled child the handlers sit on the wrapper span, so there is
  // nothing to chain; otherwise the child's own handler runs first. An inert
  // tooltip (disabled, or nothing to say) never wraps: the span would only
  // change the layout for no benefit.
  const inert = disabled || (!truncate && !hasContent);
  const childDisabled = !!child.props.disabled && !inert;
  const own = childDisabled ? {} : child.props;
  const handlers = {
    onPointerEnter: (e) => {
      own.onPointerEnter?.(e);
      if (e.pointerType === "touch") return;
      clear("close");
      if (open || suppressed.current) return;
      if (groupIsWarm()) show("hover");
      else timers.current.open = setTimeout(() => show("hover"), delay);
    },
    onPointerLeave: (e) => {
      own.onPointerLeave?.(e);
      if (e.pointerType === "touch") {
        clear("press");
        return;
      }
      clear("open");
      suppressed.current = false;
      if (mode.current === "hover") scheduleClose();
    },
    onPointerDown: (e) => {
      own.onPointerDown?.(e);
      if (e.pointerType === "touch") {
        clear("press");
        timers.current.press = setTimeout(() => show("touch"), LONG_PRESS_MS);
        return;
      }
      if (openOnClick) return;
      clear("open");
      suppressed.current = true;
      if (open) hide();
    },
    onPointerUp: (e) => {
      own.onPointerUp?.(e);
      clear("press");
    },
    onPointerCancel: (e) => {
      own.onPointerCancel?.(e);
      clear("press");
    },
    onClick: (e) => {
      own.onClick?.(e);
      if (openOnClick) {
        if (open && mode.current === "click") hide();
        else show("click");
        return;
      }
      if (open && mode.current !== "touch") hide();
    },
    onFocus: (e) => {
      own.onFocus?.(e);
      if (e.target !== e.currentTarget || !matchesFocusVisible(e.currentTarget)) return;
      suppressed.current = false;
      show("focus");
    },
    onBlur: (e) => {
      own.onBlur?.(e);
      suppressed.current = false;
      if (mode.current === "focus" || mode.current === "click") hide();
    },
    onContextMenu: (e) => {
      own.onContextMenu?.(e);
      // A long-press already showed the tooltip; don't also open the OS menu.
      if (mode.current === "touch") e.preventDefault();
    },
  };

  const label = child.props["aria-label"];
  const redundant = typeof body === "string" && !combos && body === label;
  const describe = !truncate && hasContent && !redundant && !disabled;
  const describedBy = [child.props["aria-describedby"], describe ? id : null]
    .filter(Boolean).join(" ") || undefined;

  const childProps = {
    title: undefined,
    "aria-describedby": describedBy,
    ...(combos && child.props["aria-keyshortcuts"] == null
      ? { "aria-keyshortcuts": ariaKeys(combos) } : null),
  };

  // JSX rather than cloneElement for the enabled case: equivalent (React 19
  // keeps `ref` in props, and it is replaced by a merged one here), and the
  // handlers stay ordinary JSX props.
  const Child = child.type;
  const trigger = childDisabled ? (
    <span ref={setTrigger} className={`${styles.disabledWrap} ${wrapperClassName}`} {...handlers}>
      {cloneElement(child, childProps)}
    </span>
  ) : (
    <Child {...child.props} {...childProps} {...handlers} ref={setTrigger} />
  );

  const reduced = prefersReducedMotion();
  const placed = pos?.side || side;
  const tipBody = (
    <>
      {body != null && body !== "" && <span className={styles.body}>{body}</span>}
      {combos && <Keys combos={combos} />}
    </>
  );

  return (
    <>
      {trigger}
      {createPortal(
        <>
          <AnimatePresence onExitComplete={() => setPresent(false)}>
            {shown && (
              <motion.div
                key="tip"
                ref={tipRef}
                id={id}
                role="tooltip"
                className={styles.tip}
                data-side={placed}
                style={{
                  top: pos?.top ?? 0,
                  left: pos?.left ?? 0,
                  visibility: pos ? "visible" : "hidden",
                  transformOrigin: ORIGIN[placed]?.(pos?.arrow ?? 0),
                }}
                initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.94 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
                transition={{ duration: reduced ? 0 : 0.13, ease: [0.22, 1, 0.36, 1] }}
                onPointerEnter={() => clear("close")}
                onPointerLeave={() => { if (mode.current === "hover") scheduleClose(); }}
                // React bubbles portal events through the component tree, so a
                // click on the bubble would otherwise reach e.g. a clickable
                // table row that contains the trigger.
                onClick={stop}
                onPointerDown={stop}
                onMouseDown={stop}
              >
                {tipBody}
                {arrow && pos && (
                  <span
                    className={styles.arrow}
                    aria-hidden="true"
                    style={placed === "top" || placed === "bottom"
                      ? { left: pos.arrow }
                      : { top: pos.arrow }}
                  />
                )}
              </motion.div>
            )}
          </AnimatePresence>
          {describe && !shown && !present && (
            <span id={id} hidden>
              {body}
              {combos && ` (${combos.map((c) => c.join("+")).join(" or ")})`}
            </span>
          )}
        </>,
        document.body,
      )}
    </>
  );
}
