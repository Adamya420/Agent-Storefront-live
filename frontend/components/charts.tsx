"use client";
import { useId, useRef, useState } from "react";
import clsx from "clsx";

/* Hand-rolled inline SVG charts — no charting library. The old console pulled
   in recharts (+ its d3 module graph) for this; the redesign's line/area/donut
   shapes are simple enough to draw directly, which measurably shrinks the
   client bundle and removes a whole dependency's hydration cost. That's a
   direct answer to "no lag or high loading times between pages". */

const TONE_VAR: Record<string, string> = { jade: "--jade", ochre: "--ochre", crimson: "--crimson", accent: "--accent" };

// Small inline trend line — used inside a HeroMetric or stat row, no axes.
export function Sparkline({ points, tone = "jade", width = 120, height = 28 }: {
  points: number[]; tone?: "jade" | "ochre" | "crimson" | "accent"; width?: number; height?: number;
}) {
  if (points.length < 2) return null;
  const min = Math.min(...points), max = Math.max(...points), span = max - min || 1;
  const d = points.map((v, i) => {
    const x = (i / (points.length - 1)) * width;
    const y = height - ((v - min) / span) * (height - 4) - 2;
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} preserveAspectRatio="none">
      <path d={d} fill="none" stroke={`rgb(var(${TONE_VAR[tone]}))`} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// The hero area chart — gradient fill, dedupes consecutive identical x labels
// (same-day session data otherwise repeats one label 5-8x — a real bug found
// and fixed in the previous console; ported forward here from the start).
//
// Interactive on hover: a crosshair + point marker + a value/date tooltip,
// tracked with plain mousemove math (nearest-point lookup), no charting
// library. Dropping recharts cut every chart page's First Load JS roughly in
// half; the tradeoff was losing its built-in tooltip, which read as the chart
// being a static placeholder — this restores that without bringing the
// dependency (and its bundle cost) back.
export function AreaTrend({ data, height = 130, tone = "jade", fmt }: {
  data: { label: string; value: number }[]; height?: number; tone?: "jade" | "ochre" | "crimson" | "accent"; fmt?: (v: number) => string;
}) {
  const gid = useId();
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  if (data.length < 2) return null;
  const width = 620;
  const min = Math.min(0, ...data.map((d) => d.value)), max = Math.max(...data.map((d) => d.value)) || 1;
  const span = max - min || 1;
  const pts = data.map((d, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((d.value - min) / span) * (height - 6) - 2;
    return { x, y };
  });
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;

  let last: string | undefined;
  const shown = data.map((d) => { const v = d.label === last ? "" : d.label; last = d.label; return v; });

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * width;
    // nearest point, not fractional interpolation — the data IS discrete sessions
    let nearest = 0, bestDist = Infinity;
    pts.forEach((p, i) => { const d = Math.abs(p.x - relX); if (d < bestDist) { bestDist = d; nearest = i; } });
    setHover(nearest);
  }

  const h = hover != null ? pts[hover] : null;
  const hd = hover != null ? data[hover] : null;
  // Keep the tooltip on-canvas near either edge (percentage-based, survives the responsive width:100% svg).
  const tipLeftPct = h ? Math.min(88, Math.max(0, (h.x / width) * 100)) : 0;
  const tipAlign = h && h.x / width > 0.72 ? "translate(-100%, 0)" : h && h.x / width < 0.1 ? "translate(0, 0)" : "translate(-50%, 0)";

  return (
    <div className="relative">
      <svg ref={svgRef} viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none"
        onMouseMove={onMove} onMouseLeave={() => setHover(null)} className="cursor-crosshair overflow-visible">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={`rgb(var(${TONE_VAR[tone]}))`} stopOpacity="0.16" />
            <stop offset="100%" stopColor={`rgb(var(${TONE_VAR[tone]}))`} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gid})`} />
        <path d={line} fill="none" stroke={`rgb(var(${TONE_VAR[tone]}))`} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        {h && (
          <>
            <line x1={h.x} y1={0} x2={h.x} y2={height} stroke="rgb(var(--line2))" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            <circle cx={h.x} cy={h.y} r="4" fill={`rgb(var(${TONE_VAR[tone]}))`} stroke="rgb(var(--bg))" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>
      {h && hd && (
        <div className="pointer-events-none absolute top-0 z-10 whitespace-nowrap rounded-[4px] border border-line bg-surface px-2 py-1 text-[11px] shadow-flyout"
          style={{ left: `${tipLeftPct}%`, transform: tipAlign }}>
          <div className="mono font-semibold text-ink">{fmt ? fmt(hd.value) : hd.value}</div>
          <div className="mono text-ink3">{hd.label}</div>
        </div>
      )}
      <div className="mono mt-1 flex justify-between text-[11px] text-ink3">
        {shown.map((l, i) => <span key={i}>{l}</span>)}
      </div>
    </div>
  );
}

// Sessions → offered → converted → recovered, as connected bars — not a card grid.
export function Funnel({ steps }: { steps: { label: string; value: number; tone: "jade" | "ochre" | "crimson" | "ink3" }[] }) {
  const max = Math.max(1, ...steps.map((s) => s.value));
  const toneClass = (t: string) => (t === "jade" ? "bg-jade" : t === "ochre" ? "bg-ochre" : t === "crimson" ? "bg-crimson" : "bg-ink3");
  return (
    <div className="stagger grid grid-cols-2 gap-5 sm:grid-cols-4">
      {steps.map((s, i) => {
        const prev = i > 0 ? steps[i - 1].value : s.value;
        const drop = prev > 0 && i > 0 ? Math.round(((prev - s.value) / prev) * 100) : null;
        return (
          <div key={s.label}>
            <div className="mb-1.5 flex items-baseline justify-between text-[12px] text-ink2">
              <span>{s.label}</span><span className="mono font-semibold text-ink">{s.value}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-line">
              <div className={clsx("h-full rounded-full transition-all duration-700 ease-swift", toneClass(s.tone))} style={{ width: `${(s.value / max) * 100}%` }} />
            </div>
            <div className="mono mt-1 text-[11px] text-ink3">{drop != null && drop > 0 ? `−${drop}%` : ""}</div>
          </div>
        );
      })}
    </div>
  );
}

// Minimal composition breakdown — bars, not a pie/donut widget-in-a-box.
export function CompositionBars({ data }: {
  // `tone` covers the common ≤4-item semantic case (pass/pending/deny/neutral).
  // `color` is an escape hatch for a categorical breakdown with MORE than 4
  // distinct items (e.g. 6 different gate-deny reason codes) — cycling only 4
  // tones there causes two unrelated categories to render in the same color,
  // a real collision bug found live during the build. Pass explicit hex values
  // (stay within one family — e.g. crimson/ochre shades for "things the gate
  // stopped" — rather than an arbitrary rainbow) when `tone` alone can't give
  // every item a distinct color.
  data: { name: string; pct: number; tone: "jade" | "ochre" | "crimson" | "ink3"; color?: string }[];
}) {
  const toneClass = (t: string) => (t === "jade" ? "bg-jade" : t === "ochre" ? "bg-ochre" : t === "crimson" ? "bg-crimson" : "bg-ink3");
  return (
    <div className="flex flex-col gap-3">
      {data.map((d) => (
        <div key={d.name}>
          <div className="mb-1 flex justify-between text-[13px]"><span>{d.name}</span><span className="mono">{d.pct}%</span></div>
          <div className="h-1.5 rounded-full bg-line">
            <div
              className={clsx("h-full rounded-full transition-all duration-700 ease-swift", !d.color && toneClass(d.tone))}
              style={{ width: `${d.pct}%`, background: d.color }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

// The merchant-band slider — thin track, functional-contrast fill vs. unfilled.
export function Slider({ label, hint, value, min, max, step = 1, suffix, onChange }: {
  label: string; hint?: string; value: number; min: number; max: number; step?: number; suffix?: string; onChange: (v: number) => void;
}) {
  const pctv = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-[13px]">{label}</span>
        <span className="mono text-[15px] font-semibold tnum">{value.toLocaleString("en-IN")}<span className="ml-1 text-[11px] font-normal text-ink3">{suffix}</span></span>
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-ink3">{hint}</div>}
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseInt(e.target.value))}
        className="obs-slider mt-3.5 h-[3px] w-full cursor-pointer appearance-none rounded-full"
        style={{ background: `linear-gradient(90deg, rgb(var(--accent)) ${pctv}%, rgb(var(--line2)) ${pctv}%)` }} />
    </div>
  );
}
