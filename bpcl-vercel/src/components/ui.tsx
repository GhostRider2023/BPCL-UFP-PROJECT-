/**
 * Primitives
 * ==========
 *
 * The small, repeated pieces of the control room: status pills, callouts,
 * section headers, the readout faces on the console rail, KPI cards, a popover,
 * a tab strip, and the form controls the popovers open into.
 *
 * They live together because they share one convention — an `accent` colour
 * that means the same thing everywhere (see `theme.ts`) — and separating them
 * into a file each would scatter that convention across a directory.
 */

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { C, alpha } from '../theme';

/* ═══════════════════════════════════════════════════════════════════
   Status
   ═══════════════════════════════════════════════════════════════════ */

export type Tone = 'ok' | 'bad' | 'warn' | 'info';

const TONE: Record<Tone, { fg: string; label: string }> = {
  ok: { fg: C.green, label: 'status' },
  bad: { fg: C.red, label: 'alert' },
  warn: { fg: C.amber, label: 'warning' },
  info: { fg: C.blue, label: 'note' },
};

export function Pill({
  tone,
  children,
  pulse = false,
}: {
  tone: Tone;
  children: ReactNode;
  pulse?: boolean;
}) {
  const { fg } = TONE[tone];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[10.5px] font-medium whitespace-nowrap"
      style={{ color: fg, borderColor: alpha(fg, 0.32), background: alpha(fg, 0.09) }}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${pulse ? 'animate-pulse-soft' : ''}`}
        style={{ background: fg }}
      />
      {children}
    </span>
  );
}

export function Callout({
  tone,
  children,
  className = '',
}: {
  tone: Tone;
  children: ReactNode;
  className?: string;
}) {
  const { fg } = TONE[tone];
  return (
    <div
      role={tone === 'bad' ? 'alert' : undefined}
      className={`animate-fade-up rounded-xl border px-4 py-3 text-[12.5px] leading-relaxed ${className}`}
      style={{
        borderColor: alpha(fg, 0.34),
        background: alpha(fg, 0.07),
        borderLeft: `3px solid ${fg}`,
      }}
    >
      {children}
    </div>
  );
}

export function Section({ title, note }: { title: string; note?: string }) {
  return (
    <div className="mt-7 mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2 className="text-[11px] font-semibold tracking-[0.15em] text-ink uppercase">{title}</h2>
      {note && <span className="text-[11px] text-muted">{note}</span>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   Console rail readout
   ───────────────────────────────────────────────────────────────────
   A slot READS BACK the value currently loaded into the model, and opens into
   the control that sets it. A sidebar shows widgets and never shows state; a
   control panel shows state and hands you a control when you reach for one.
   This is the second kind — which is also why the whole input vector stays on
   the same screen as the output, where it belongs when comparing runs.
   ═══════════════════════════════════════════════════════════════════ */

export function Readout({
  label,
  value,
  unit,
  accent,
  sub,
  open,
  interactive = false,
}: {
  label: string;
  value: string;
  unit?: string;
  accent: string;
  sub?: ReactNode;
  open?: boolean;
  interactive?: boolean;
}) {
  return (
    <div
      className="group relative h-full overflow-hidden rounded-lg border px-3.5 py-3 text-left transition-colors duration-150"
      style={{
        borderColor: open ? alpha(accent, 0.5) : C.border,
        background: open ? alpha(accent, 0.07) : C.panel,
      }}
    >
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-[2px] transition-opacity duration-150"
        style={{ background: accent, opacity: open ? 1 : 0.45 }}
      />
      <div className="flex items-center gap-2">
        <span className="truncate text-[9.5px] font-semibold tracking-[0.14em] text-muted uppercase">
          {label}
        </span>
        {interactive && (
          <svg
            aria-hidden
            viewBox="0 0 10 6"
            className="ml-auto h-[5px] w-[9px] shrink-0 opacity-40 transition-opacity group-hover:opacity-90"
            fill="none"
            stroke={C.muted}
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M1 1l4 4 4-4" />
          </svg>
        )}
      </div>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span className="num truncate text-[19px] leading-none font-semibold" style={{ color: accent }}>
          {value}
        </span>
        {unit && <span className="text-[10.5px] font-medium text-muted">{unit}</span>}
      </div>
      {sub && <div className="mt-1.5 truncate text-[10.5px] text-muted">{sub}</div>}
    </div>
  );
}

/** Highlights the part of a readout's subtitle that is doing the work. */
export function Hot({ children }: { children: ReactNode }) {
  return <span style={{ color: C.gold, fontWeight: 600 }}>{children}</span>;
}

/* ═══════════════════════════════════════════════════════════════════
   KPI card
   ═══════════════════════════════════════════════════════════════════ */

export function Kpi({
  label,
  value,
  unit,
  delta,
  direction = 'none',
  accent,
}: {
  label: string;
  value: string;
  unit?: string;
  delta?: ReactNode;
  direction?: 'up' | 'down' | 'flat' | 'none';
  accent: string;
}) {
  return (
    <div
      className="panel animate-fade-up relative overflow-hidden px-4 py-3.5"
      style={{ borderColor: alpha(accent, 0.24) }}
    >
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-[2px]"
        style={{ background: `linear-gradient(90deg, ${accent}, transparent 88%)` }}
      />
      <div className="text-[9.5px] font-semibold tracking-[0.14em] text-muted uppercase">
        {label}
      </div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className="num text-[26px] leading-none font-semibold" style={{ color: accent }}>
          {value}
        </span>
        {unit && <span className="text-[11px] font-medium text-muted">{unit}</span>}
      </div>
      {delta && (
        <div className="num mt-2 flex items-center gap-1.5 text-[10.5px] text-muted">
          {direction !== 'none' && <Trend direction={direction} colour={accent} />}
          <span className="truncate">{delta}</span>
        </div>
      )}
    </div>
  );
}

function Trend({
  direction,
  colour,
}: {
  direction: 'up' | 'down' | 'flat';
  colour: string;
}) {
  if (direction === 'flat') {
    return (
      <svg aria-hidden viewBox="0 0 8 8" className="h-2 w-2 shrink-0" stroke={colour} strokeWidth="1.4">
        <path d="M1 4h6" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg aria-hidden viewBox="0 0 8 8" className="h-2 w-2 shrink-0" fill={colour}>
      <path d={direction === 'up' ? 'M4 1l3.2 5.4H0.8z' : 'M4 7L0.8 1.6h6.4z'} />
    </svg>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   Popover
   ───────────────────────────────────────────────────────────────────
   Anchored to its trigger, dismissed on outside click or Escape, and flipped
   left when it would otherwise run off the right edge — which the rightmost
   two console slots always would.
   ═══════════════════════════════════════════════════════════════════ */

export function Popover({
  trigger,
  children,
  width = 320,
  title,
}: {
  trigger: (open: boolean) => ReactNode;
  children: ReactNode;
  width?: number;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [alignRight, setAlignRight] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!host.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !host.current) return;
    const { left } = host.current.getBoundingClientRect();
    setAlignRight(left + width + 16 > window.innerWidth);
  }, [open, width]);

  return (
    <div ref={host} className="relative h-full">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="block h-full w-full cursor-pointer text-left"
      >
        {trigger(open)}
      </button>

      {open && (
        <div
          ref={panel}
          role="dialog"
          aria-label={title}
          className="panel animate-fade-up absolute z-40 mt-2 p-4"
          style={{
            width,
            maxWidth: 'calc(100vw - 32px)',
            [alignRight ? 'right' : 'left']: 0,
            boxShadow: '0 20px 50px rgb(0 0 0 / 0.6)',
          }}
        >
          {title && <div className="rail-label mb-3">{title}</div>}
          <div className="space-y-4">{children}</div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   Tabs
   ═══════════════════════════════════════════════════════════════════ */

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="tablist" className="flex flex-wrap gap-0.5 border-b border-edge">
      {tabs.map((tab) => {
        const on = tab.id === active;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={on}
            onClick={() => onChange(tab.id)}
            className={`relative cursor-pointer px-4 py-2.5 text-[12px] font-medium tracking-[0.01em] transition-colors ${
              on ? 'text-ink' : 'text-muted hover:text-ink'
            }`}
          >
            {tab.label}
            {on && (
              <span
                aria-hidden
                className="absolute inset-x-3 -bottom-px h-[2px]"
                style={{ background: C.gold }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   Form controls
   ═══════════════════════════════════════════════════════════════════ */

export function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11px] font-semibold text-ink">{label}</span>
      {children}
      {help && <span className="mt-1.5 block text-[10.5px] leading-snug text-muted">{help}</span>}
    </label>
  );
}

export function Slider({
  value,
  min,
  max,
  step,
  accent = C.gold,
  unit,
  decimals = 0,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  accent?: string;
  unit?: string;
  decimals?: number;
  onChange: (value: number) => void;
}) {
  const fill = max === min ? 0 : ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="mb-0.5 flex items-baseline justify-between">
        <span className="num text-[15px] font-bold" style={{ color: accent }}>
          {value.toFixed(decimals)}
          {unit && <span className="ml-1 text-[10px] font-medium text-muted">{unit}</span>}
        </span>
        <span className="num text-[9.5px] text-muted">
          {min.toFixed(decimals)} – {max.toFixed(decimals)}
        </span>
      </div>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        style={
          {
            '--range-accent': accent,
            '--range-fill': `${fill}%`,
          } as React.CSSProperties
        }
      />
    </div>
  );
}

export function NumberField({
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  onChange: (value: number) => void;
}) {
  // Held as a string while focused so the field can pass through the
  // intermediate states typing produces ("", "7", "7.") without the value
  // snapping back on every keystroke. Committed — and clamped — on blur.
  const [draft, setDraft] = useState<string | null>(null);
  const commit = useCallback(
    (raw: string) => {
      setDraft(null);
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) onChange(Math.min(Math.max(parsed, min), max));
    },
    [min, max, onChange],
  );

  return (
    <div className="flex items-center gap-2">
      <input
        type="number"
        className="num w-full rounded-lg border border-edge bg-bg/70 px-2.5 py-1.5 text-[13px] font-semibold text-ink transition-colors focus:border-gold"
        value={draft ?? value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
      />
      {unit && <span className="shrink-0 text-[10.5px] text-muted">{unit}</span>}
    </div>
  );
}

export function Select<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  const numeric = typeof value === 'number';
  return (
    <select
      className="w-full cursor-pointer rounded-lg border border-edge bg-bg/70 px-2.5 py-1.5 text-[13px] font-semibold text-ink transition-colors focus:border-gold"
      value={value}
      onChange={(e) => onChange((numeric ? Number(e.target.value) : e.target.value) as T)}
    >
      {options.map((option) => (
        <option key={String(option.value)} value={option.value} className="bg-panel">
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function Toggle({
  checked,
  label,
  help,
  onChange,
}: {
  checked: boolean;
  label: string;
  help?: string;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-2.5">
      <button
        type="button"
        id={id}
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className="mt-0.5 h-[18px] w-[32px] shrink-0 cursor-pointer rounded-full border transition-colors duration-150"
        style={{
          borderColor: checked ? alpha(C.green, 0.6) : C.border,
          background: checked ? alpha(C.green, 0.28) : '#131c2e',
        }}
      >
        <span
          className="block h-[12px] w-[12px] rounded-full transition-transform duration-150"
          style={{
            background: checked ? C.green : C.muted,
            transform: `translateX(${checked ? 16 : 2}px)`,
          }}
        />
      </button>
      <label htmlFor={id} className="cursor-pointer select-none">
        <span className="block text-[11.5px] font-semibold text-ink">{label}</span>
        {help && <span className="mt-0.5 block text-[10.5px] leading-snug text-muted">{help}</span>}
      </label>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   Formatting
   ═══════════════════════════════════════════════════════════════════ */

/**
 * Render a number, or an em dash when there is none.
 *
 * Absence is a real state in this model — `T_air_C` is null everywhere except
 * the two terminals — and it has to look like absence rather than like zero.
 */
export function num(
  value: number | null | undefined,
  decimals = 1,
  options: { group?: boolean; sign?: boolean } = {},
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const text = options.group
    ? value.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    : value.toFixed(decimals);
  return options.sign && value >= 0 ? `+${text}` : text;
}
