# Design

Visual system for the Stock Signal terminal. Dark-first, monospace-led, one committed accent. The metaphor is a pro trading desk: a strict ruled grid carries a maximal, vivid surface so intensity never reads as chaos.

## Theme

Dark, non-negotiable. Scene: a self-directed ADHD investor scanning market mood and their own positions on a large monitor in a dim home office before the open, wanting to feel in command. Light theme is a supported fallback but the design is tuned for dark.

## Color

Strategy: **Committed**. A warm amber phosphor accent carries brand, selection, focus, and "live" state across the surface. Data direction is a separate, fixed semantic pair (up = mint, down = crimson) so the accent never competes with price meaning. Amber is deliberately chosen to dodge the four reflexes: not SaaS purple, not corporate gold-on-navy, not crypto neon-green, not console cyan.

All values OKLCH. Neutrals are tinted a hair warm (hue ~80, chroma ≤ 0.011) so the whole surface reads as one family. Never `#000` / `#fff`.

Dark (primary):
- `--bg`: oklch(0.145 0.007 80)
- `--surface`: oklch(0.183 0.008 80)
- `--surface-2`: oklch(0.223 0.009 80)
- `--surface-3`: oklch(0.265 0.010 80)  (raised: menus, active row)
- `--grid`: oklch(1 0 0 / 0.055)  (hairline ruling, the terminal skeleton)
- `--border`: oklch(1 0 0 / 0.11)
- `--border-strong`: oklch(1 0 0 / 0.22)
- `--text`: oklch(0.945 0.006 80)
- `--text-muted`: oklch(0.72 0.010 80)
- `--text-faint`: oklch(0.56 0.012 80)
- `--accent`: oklch(0.82 0.145 75)   (amber phosphor)
- `--accent-strong`: oklch(0.76 0.16 68)
- `--accent-weak`: oklch(0.82 0.145 75 / 0.15)
- `--accent-glow`: oklch(0.82 0.145 75 / 0.35)  (used only for focus/live glow)
- `--positive`: oklch(0.84 0.17 158)  (mint, price up)
- `--positive-weak`: oklch(0.84 0.17 158 / 0.16)
- `--negative`: oklch(0.64 0.215 20)  (crimson, price down)
- `--negative-weak`: oklch(0.64 0.215 20 / 0.16)
- `--info`: oklch(0.74 0.11 232)  (cool, neutral/secondary series)

Light (fallback): same roles, lightness inverted, chroma trimmed ~15%, accent darkened to oklch(0.62 0.15 68) for AA on light.

Semantics: up/down is never color-only. Always pair with a glyph (▲ ▼), sign, or position so it survives color-blindness and the dyslexia mode.

## Typography

Monospace-led, the terminal signature.
- `--mono`: ui-monospace, "SF Mono", "Cascadia Code", "Segoe UI Mono", "Roboto Mono", Consolas, monospace. Carries all numbers, tickers, labels, table data, most UI chrome. `font-variant-numeric: tabular-nums` everywhere numbers align.
- `--sans`: system-ui, "Segoe UI", Roboto, sans-serif. Prose only: news bodies, help text, longer copy. Capped 68ch.
- Labels are uppercase mono, letter-spacing 0.08em, `--text-faint`, small (11px). This "terminal caption" is the primary hierarchy device.
- Scale (fixed rem, ratio ~1.2): 11 / 12.5 / 14 / 17 / 22 / 30 / 44. Big numbers (gauge score, position P/L) use the top of the scale in mono, weight 600.
- Hierarchy from weight + case + color, not many sizes.

## Layout

- App shell: slim fixed left rail (icon + mono label) with exactly the five modules, a market-lean status strip pinned top, a live ticker tape below it. Rail footer holds only a Settings gear (utility, not a module).
- Every view is a **ruled grid**: 1px `--grid` hairlines divide regions (no floating cards). Regions are labeled with the uppercase-mono caption in their top-left, like terminal panes.
- Density is a feature: tables run edge to edge, rows are compact (32-36px), zebra via `--surface`/`--surface-2` at very low delta.
- Avoid the SaaS card grid entirely. Use ruled panes, split columns, and full-bleed tables. No nested containers.
- Responsive is structural: rail collapses to icon-only, multi-column panes stack.
  Fixed breakpoints (custom properties can't be used inside `@media`):
  - 1000px: two-column panes stack.
  - 900px: the rail drops to icons.
  - 640px (phone): the rail is replaced by a bottom tab bar (`MobileNav`), with the
    five core views plus a "More" sheet. The top bar is one row (title, search,
    alerts, account). Refresh, theme, dyslexia and tour move into the account
    menu. Wide tables either stack into cards (Portfolio) or scroll sideways
    behind a `ScrollHint` edge fade.
  - 560px: a small phone.
- Touch: under `(pointer: coarse)` every control in `main` has at least `--tap`
  (40px) of hit height (one rule in `index.css`). Icon-only buttons also take
  the width in their own module. Inline glyphs (InfoTip) keep their size and
  grow an invisible hit area instead.
- Type scale tokens: `--fs-caption` 11, `--fs-small` 12.5, `--fs-body` 14,
  `--fs-lead` 17, `--fs-h2` 22, `--fs-h1` 30, `--fs-display` 44.

## Elevation

Mostly flat. Depth via hairline borders and a faint 1px top highlight, not drop shadows. Raised surfaces (command palette, menus) get `--surface-3` + a single soft shadow. No glassmorphism. The only glow in the system is the amber focus/live glow, used sparingly.

## Components

Consistent vocabulary, every state (default / hover / focus / active / disabled / loading / error):
- **Stat cell**: mono label (caption) + big tabular number + signed delta with ▲/▼ in positive/negative. The atomic unit; never a gradient hero tile.
- **Meter bar**: inline block-bar (`████░░░░`) or thin track, used for conviction, gauges, allocation. Amber or semantic fill.
- **Fear→Greed gauge**: the hero of Market Sentiment. A wide horizontal spectrum with an animated amber needle and a big mono score.
- **Ticker tape**: horizontally scrolling live quotes, mono, colored deltas, pausable, respects reduce-motion.
- **Data table**: sticky mono header (uppercase caption), tabular-nums, row hover = `--surface-3`, right-aligned numerics, inline sparklines.
- **Source badge**: every data region shows a live/stale/error dot + "updated Ns ago" (honest state, never hidden).
- **Command palette (⌘K)**: keyboard jump to any of the five views and quick actions. Terminal-native, ADHD quick-nav.
- Loading = skeleton rows/shimmer, never center spinners. Empty states teach ("add holdings to see P/L"), never blank.

## Motion

Maximal but meaningful, and fully collapsible under `prefers-reduced-motion` / the reduce-motion setting.
- Timing 120-220ms, ease-out-expo / quart. No bounce.
- Numbers count-up / roll on change; fresh data shimmers once; the gauge needle eases to position; "live" dots pulse; the ticker tape scrolls.
- Never animate layout properties; transform/opacity only.
- Reduce-motion collapses all of the above to instant state changes; nothing essential is motion-only.

## Signature

If you removed the amber and the monospace ruling, it should stop looking like this product. The tell: ruled terminal panes with uppercase-mono captions, tabular numbers with ▲/▼ deltas, an amber live-glow, and a hero Fear→Greed gauge.
