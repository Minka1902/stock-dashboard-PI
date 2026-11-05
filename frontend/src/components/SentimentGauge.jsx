import { useEffect, useRef } from "react";
import { animate } from "animejs";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./SentimentGauge.module.css";

const CENTER = 150;
const PIVOT_Y = 150;

/**
 * Fear & Greed as a semicircular gauge.
 *
 * The needle and the score are driven by animejs off one timeline, so the
 * number and the pointer always agree mid-flight — with motion they read as a
 * single instrument settling, and with reduced motion they simply snap.
 */
export default function SentimentGauge({ score, rating }) {
  const has = score != null;
  const value = has ? Math.max(0, Math.min(100, score)) : 50;
  const needleRef = useRef(null);
  const numRef = useRef(null);

  useEffect(() => {
    const needle = needleRef.current;
    const num = numRef.current;
    const target = { v: 0 };
    const apply = (v) => {
      if (needle) needle.style.transform = `rotate(${(v - 50) * 1.8}deg)`;
      if (num) num.textContent = has ? String(Math.round(v)) : "--";
    };

    if (!has) { apply(50); return undefined; }
    if (prefersReducedMotion()) { apply(value); return undefined; }

    const anim = animate(target, {
      v: value,
      duration: 1100,
      ease: "outElastic(1, 0.75)",
      onUpdate: () => apply(target.v),
    });
    return () => anim.pause();
  }, [value, has]);

  return (
    <div className={styles.gauge}>
      <svg
        viewBox="0 0 300 168"
        className={styles.svg}
        role="img"
        aria-label={has ? `Fear and Greed ${Math.round(value)}, ${rating || ""}` : "Fear and Greed, no data"}
      >
        <defs>
          {/* Fear (left) → Greed (right): red → amber → green. */}
          <linearGradient id="fgArcV2" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--negative)" />
            <stop offset="50%" stopColor="var(--accent)" />
            <stop offset="100%" stopColor="var(--positive)" />
          </linearGradient>
        </defs>

        <path d="M 30 150 A 120 120 0 0 1 270 150" fill="none"
              stroke="var(--surface-3)" strokeWidth="16" strokeLinecap="round" />
        <path d="M 30 150 A 120 120 0 0 1 270 150" fill="none"
              stroke="url(#fgArcV2)" strokeWidth="14" strokeLinecap="round"
              opacity={has ? 1 : 0.28} />

        {[0, 25, 50, 75, 100].map((t) => {
          const a = (Math.PI * (100 - t)) / 100;
          return (
            <line
              key={t}
              x1={CENTER + Math.cos(a) * 132} y1={PIVOT_Y - Math.sin(a) * 132}
              x2={CENTER + Math.cos(a) * 120} y2={PIVOT_Y - Math.sin(a) * 120}
              stroke="var(--grid)" strokeWidth="2"
            />
          );
        })}

        {has && (
          <g ref={needleRef} className={styles.needle}>
            <line x1="150" y1="150" x2="150" y2="42"
                  stroke="var(--accent)" strokeWidth="3.5" strokeLinecap="round" />
            <circle cx="150" cy="42" r="4" fill="var(--accent)" />
          </g>
        )}
        <circle cx="150" cy="150" r="8" fill="var(--surface-3)"
                stroke="var(--accent)" strokeWidth="2" />
      </svg>

      <div className={styles.score}>
        <span className={styles.num} ref={numRef}>{has ? Math.round(value) : "--"}</span>
        <span className={styles.rating}>{has ? (rating || "") : "no data"}</span>
      </div>
      <div className={styles.ends}>
        <span>Extreme fear</span><span>Extreme greed</span>
      </div>
    </div>
  );
}
