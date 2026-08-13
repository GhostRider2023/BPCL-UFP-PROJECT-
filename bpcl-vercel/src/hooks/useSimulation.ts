import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type Meta, type Params, type SimResult } from '../api';

/**
 * The run loop
 * ============
 *
 * Streamlit re-ran the whole script on every widget change. Here the console is
 * local state and a run is one POST, debounced so that dragging a slider issues
 * one request at the end of the gesture rather than sixty during it.
 *
 * Two details matter for correctness rather than for speed:
 *
 *   1. In-flight runs are aborted when the parameters change again. Without
 *      that, a slow request started at 300 m3/hr can land after a fast one
 *      started at 400 and repaint the screen with numbers that do not match the
 *      console — the classic stale-response race.
 *
 *   2. The previous result is kept on screen while the next one is computing,
 *      with `pending` raised. Blanking the dashboard on every keystroke makes it
 *      unusable for comparing runs, which is the main thing it is for.
 */

const DEBOUNCE_MS = 220;

export interface SimulationState {
  meta: Meta | null;
  params: Params | null;
  result: SimResult | null;
  /** A run is in flight. The previous result stays on screen underneath. */
  pending: boolean;
  /** Fatal: no meta, so the console cannot even be drawn. */
  fatal: string | null;
  /** Non-fatal: the last run failed, but there is still something on screen. */
  runError: string | null;
  activePreset: string | null;
}

export function useSimulation() {
  const [state, setState] = useState<SimulationState>({
    meta: null,
    params: null,
    result: null,
    pending: true,
    fatal: null,
    runError: null,
    activePreset: 'Baseline',
  });

  const inFlight = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Bumped by the air refresh control so the next run bypasses the server-side
  // TTL cache. Held in a ref because it must not itself trigger a run.
  const refreshAir = useRef(false);

  /* ── Boot ─────────────────────────────────────────────────────── */

  useEffect(() => {
    let cancelled = false;
    api
      .meta()
      .then((meta) => {
        if (cancelled) return;
        setState((s) => ({ ...s, meta, params: meta.defaults }));
      })
      .catch((error: Error) => {
        if (cancelled) return;
        setState((s) => ({ ...s, pending: false, fatal: error.message }));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /* ── Run whenever the parameters settle ───────────────────────── */

  const params = state.params;

  useEffect(() => {
    if (!params) return;

    if (timer.current) clearTimeout(timer.current);
    setState((s) => ({ ...s, pending: true }));

    timer.current = setTimeout(() => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;

      const force = refreshAir.current;
      refreshAir.current = false;

      api
        .simulate(params, { refreshAir: force, signal: controller.signal })
        .then((result) => {
          if (controller.signal.aborted) return;
          setState((s) => ({ ...s, result, pending: false, runError: null }));
        })
        .catch((error: Error) => {
          // An abort is this hook superseding its own request, not a failure.
          if (controller.signal.aborted || error.name === 'AbortError') return;
          setState((s) => ({ ...s, pending: false, runError: error.message }));
        });
    }, DEBOUNCE_MS);

    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [params]);

  /* ── Controls ─────────────────────────────────────────────────── */

  const update = useCallback((patch: Partial<Params>) => {
    setState((s) =>
      s.params ? { ...s, params: { ...s.params, ...patch }, activePreset: null } : s,
    );
  }, []);

  const applyPreset = useCallback((name: string) => {
    setState((s) => {
      if (!s.meta || !s.params) return s;
      const preset = s.meta.presets.find((p) => p.name === name);
      if (!preset) return s;
      // A preset is a starting point, not a mode: Baseline restores the
      // defaults wholesale, and anything else layers its values onto them.
      // Every value stays editable afterwards, so the highlighted chip means
      // "last loaded", not "currently in".
      return {
        ...s,
        params: { ...s.meta.defaults, ...preset.values },
        activePreset: name,
      };
    });
  }, []);

  const refreshAirNow = useCallback(() => {
    refreshAir.current = true;
    // Re-issue the current run rather than fetching the reading on its own: a
    // new air temperature is a new boundary condition, so it is a new answer,
    // and showing the fresh number beside stale physics would be a lie.
    setState((s) => (s.params ? { ...s, params: { ...s.params } } : s));
  }, []);

  return { ...state, update, applyPreset, refreshAirNow };
}
