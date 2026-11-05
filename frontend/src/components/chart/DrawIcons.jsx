// 18x18 line glyphs for the drawing rail and chart actions (currentColor).
// Kept separate from components/Icon.jsx: these are chart-tool pictograms
// drawn on an 18px grid, not the 24px app icon set.

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.4, strokeLinecap: "round", strokeLinejoin: "round" };
const dot = (x, y) => <circle cx={x} cy={y} r="1.6" fill="currentColor" stroke="none" />;

const GLYPHS = {
  cursor: <path {...S} d="M4 3l9 5.2-4 1.1 2.4 4.6-1.6.8-2.4-4.6L4.3 13z" />,
  trendline: <g {...S}><path d="M3.5 14.5l11-11" />{dot(3.5, 14.5)}{dot(14.5, 3.5)}</g>,
  tray: <g {...S}><path d="M4 14l12-10" />{dot(4, 14)}{dot(9, 9.8)}</g>,
  extline: <g {...S}><path d="M1.5 16l15-14" />{dot(6, 11.8)}{dot(12, 6.2)}</g>,
  hline: <g {...S}><path d="M1.5 9h15" />{dot(9, 9)}</g>,
  ray: <g {...S}><path d="M5 9h11.5" />{dot(5, 9)}</g>,
  vline: <g {...S}><path d="M9 1.5v15" />{dot(9, 9)}</g>,
  arrow: <g {...S}><path d="M3.5 14.5l10-10" /><path d="M8.5 4h5.5v5.5" /></g>,
  channel: <g {...S}><path d="M2 12l10-7" /><path d="M6 15l10-7" /><path d="M4 13.5l10-7" strokeDasharray="1.2 2" strokeWidth="1" /></g>,
  fib: <g {...S}><path d="M2 3.5h14M2 7h14M2 10h14M2 14.5h14" /><path d="M3.5 14.5l11-11" strokeDasharray="1.5 2" strokeWidth="1" /></g>,
  fibext: <g {...S}><path d="M2 14l4-6 3 3" /><path d="M9 4h7M9 7h7M9 10.5h7" /></g>,
  zone: <rect {...S} x="3" y="4.5" width="12" height="9" rx="0.6" />,
  ellipse: <ellipse {...S} cx="9" cy="9" rx="6.5" ry="4.5" />,
  brush: <g {...S}><path d="M2.5 13c2-3 3.5 1 5.5-2s2.5-5 4.5-6 3 0 3 0" /></g>,
  text: <g {...S}><path d="M4 4.5h10M9 4.5v10" /><path d="M7 14.5h4" /></g>,
  callout: <g {...S}><rect x="6" y="2.5" width="10" height="7" rx="1.2" /><path d="M8 9.5L3 15" />{dot(3, 15)}</g>,
  measure: <g {...S}><path d="M3 15V3M3 15h12" /><path d="M6 12l7-7M10.5 5H13v2.5" /></g>,
  long: <g {...S}><rect x="3" y="3" width="12" height="6" rx="0.5" /><rect x="3" y="9" width="12" height="4" rx="0.5" strokeDasharray="1.5 1.5" /><path d="M9 7.5V4.2M7.5 5.5L9 4l1.5 1.5" /></g>,
  short: <g {...S}><rect x="3" y="9" width="12" height="6" rx="0.5" /><rect x="3" y="5" width="12" height="4" rx="0.5" strokeDasharray="1.5 1.5" /><path d="M9 10.5v3.3M7.5 12.5L9 14l1.5-1.5" /></g>,
  magnet: <g {...S}><path d="M4 3v6a5 5 0 0 0 10 0V3" /><path d="M4 6h3M11 6h3" /><path d="M7 3v6a2 2 0 0 0 4 0V3" /></g>,
  lock: <g {...S}><rect x="4" y="8" width="10" height="7.5" rx="1.2" /><path d="M6 8V5.5a3 3 0 0 1 6 0V8" /></g>,
  unlock: <g {...S}><rect x="4" y="8" width="10" height="7.5" rx="1.2" /><path d="M6 8V5.5a3 3 0 0 1 5.8-1" /></g>,
  eye: <g {...S}><path d="M1.5 9s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z" /><circle cx="9" cy="9" r="2.2" /></g>,
  eyeOff: <g {...S}><path d="M1.5 9s2.8-5 7.5-5c1.4 0 2.6.4 3.7 1M16.5 9s-2.8 5-7.5 5c-1.4 0-2.6-.4-3.7-1" /><path d="M3 15L15 3" /></g>,
  undo: <g {...S}><path d="M6 4L3 7l3 3" /><path d="M3 7h7.5a4.5 4.5 0 0 1 0 9H7" /></g>,
  redo: <g {...S}><path d="M12 4l3 3-3 3" /><path d="M15 7H7.5a4.5 4.5 0 0 0 0 9H11" /></g>,
  trash: <g {...S}><path d="M3 5h12M7 5V3.5h4V5M5 5l.8 10.5h6.4L13 5" /></g>,
  camera: <g {...S}><path d="M2.5 6.5a1.5 1.5 0 0 1 1.5-1.5h2L7.2 3h3.6L12 5h2a1.5 1.5 0 0 1 1.5 1.5V14a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 14z" /><circle cx="9" cy="10" r="2.8" /></g>,
  drafts: <g {...S}><path d="M4 2.5h7l3 3v10H4z" /><path d="M11 2.5v3h3" /><path d="M6.5 9h5M6.5 12h3.5" /></g>,
  save: <g {...S}><path d="M3 3h9.5L15 5.5V15H3z" /><path d="M6 3v4h5V3M6 15v-5h6v5" /></g>,
  share: <g {...S}><circle cx="13.5" cy="4" r="2" /><circle cx="4.5" cy="9" r="2" /><circle cx="13.5" cy="14" r="2" /><path d="M6.3 8l5.4-3M6.3 10l5.4 3" /></g>,
  copy: <g {...S}><rect x="6" y="6" width="9" height="9" rx="1.2" /><path d="M12 6V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h2" /></g>,
  download: <g {...S}><path d="M9 2.5v9M5.5 8L9 11.5 12.5 8" /><path d="M3 13v2.5h12V13" /></g>,
  edit: <g {...S}><path d="M3 15l.8-3.2L12 3.6 14.4 6l-8.2 8.2z" /></g>,
  chevron: <path {...S} d="M7 5l4 4-4 4" />,
};

export default function DrawIcon({ name, size = 18 }) {
  return (
    <svg viewBox="0 0 18 18" width={size} height={size} aria-hidden="true" focusable="false">
      {GLYPHS[name] || GLYPHS.cursor}
    </svg>
  );
}
