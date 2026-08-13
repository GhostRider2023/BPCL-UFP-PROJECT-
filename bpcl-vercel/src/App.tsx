/**
 * MMBL Thermal-Hydraulic Simulator
 * =================================
 *
 * Simulates how a petroleum batch evolves — thermally and hydraulically — as it
 * travels the buried MMBL line from Bina to Bijwasan, and presents the full
 * state at every kilometre.
 *
 * This is a SIMULATOR. It is not a leak detector and not an accounting
 * reconciliation engine. It answers one question: given a product dispatched at
 * a temperature, volume, flow rate and pressure, in a given month — what is its
 * physical state at every point on the route?
 *
 * Layout, top to bottom: masthead, dispatch console, live air, KPI row, mimic
 * diagram, then the detail tabs. The console sits above the results rather than
 * beside them so the input vector and the output stay on one screen.
 */

import { lazy, Suspense, useState } from 'react';
import { api } from './api';
import { HydraulicCharts, ThermalCharts } from './components/Charts';
import { ConsoleRail } from './components/ConsoleRail';
import { Masthead } from './components/Masthead';
import { Mimic } from './components/Mimic';
import { StationTable } from './components/StationTable';
import { useSimulation } from './hooks/useSimulation';
import { C } from './theme';
import { Callout, Kpi, Readout, Section, Tabs, num } from './components/ui';

// The map pulls in Leaflet and its CSS, and the model notes are a wall of prose.
// Neither is needed for the first paint, and the first paint is the physics.
const RouteMap = lazy(() =>
  import('./components/RouteMap').then((m) => ({ default: m.RouteMap })),
);
const ModelNotes = lazy(() =>
  import('./components/ModelNotes').then((m) => ({ default: m.ModelNotes })),
);

type TabId = 'thermal' | 'hydraulics' | 'stations' | 'route' | 'model';

const TABS: { id: TabId; label: string }[] = [
  { id: 'thermal', label: 'Thermal & Volume' },
  { id: 'hydraulics', label: 'Hydraulics' },
  { id: 'stations', label: 'Station Report' },
  { id: 'route', label: 'Route' },
  { id: 'model', label: 'Model' },
];

export default function App() {
  const { meta, params, result, pending, fatal, runError, activePreset, update, applyPreset, refreshAirNow } =
    useSimulation();
  const [tab, setTab] = useState<TabId>('thermal');
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  /* ── Cannot start ─────────────────────────────────────────────── */

  if (fatal) {
    return (
      <main className="mx-auto max-w-2xl px-5 py-20">
        <Callout tone="bad">
          <b>Cannot start.</b> The route geometry or the ERA5 soil profile failed to load:
          <br />
          <code className="num mt-1.5 block text-[11px]">{fatal}</code>
          <p className="mt-3 text-[12px]">
            The simulator does not fall back to synthetic climate data. Check that the route and soil
            data files are present in the deployment.
          </p>
        </Callout>
      </main>
    );
  }

  if (!meta || !params) {
    return (
      <main className="mx-auto max-w-7xl px-5 py-6">
        <Masthead result={null} meta={null} pending />
        <div className="panel animate-pulse-soft h-64" />
      </main>
    );
  }

  const air = result?.air ?? null;
  const terminals = meta.terminals;

  const handleExport = async () => {
    if (!params) return;
    setExporting(true);
    setExportError(null);
    try {
      await api.exportCsv(params);
    } catch (error) {
      setExportError((error as Error).message);
    } finally {
      setExporting(false);
    }
  };

  return (
    <main className="mx-auto max-w-[1600px] px-4 pt-4 pb-14 sm:px-6">
      <Masthead result={result} meta={meta} pending={pending} />

      <ConsoleRail
        params={params}
        meta={meta}
        activePreset={activePreset}
        onChange={update}
        onPreset={applyPreset}
      />

      {/* ═══════════════════════════════════════════════════════════
          LIVE AIR — BINA AND BIJWASAN

          The soil temperature is a monthly ERA5 climatology; the air
          temperature is today's, measured. Their mean is the environment the
          energy equation couples to — at the two terminals, which are the only
          places the line surfaces and therefore the only places air is queried
          at all. Everything in this block is built so a failed request is
          impossible to mistake for a reading.
          ═══════════════════════════════════════════════════════════ */}

      <div className="mt-5 mb-2 flex flex-wrap items-center justify-between gap-3">
        <span className="rail-label">Live air temperature · {terminals.join(' & ')}</span>
        <button
          type="button"
          onClick={refreshAirNow}
          disabled={pending}
          className="cursor-pointer rounded-lg border border-edge bg-panel/70 px-3 py-1 text-[10.5px] font-semibold text-muted transition-colors hover:border-gold/45 hover:text-ink disabled:cursor-wait disabled:opacity-50"
          title="Re-query both terminals and re-run with the new boundary condition"
        >
          Refresh
        </button>
      </div>

      {result?.airError && (
        <Callout tone="bad" className="mb-2">
          <b>No live air temperature.</b> This run uses the ERA5 soil temperature alone; no air
          temperature has been estimated to stand in for it.{' '}
          <code className="num text-[11px]">{result.airError}</code>
        </Callout>
      )}

      {air && Object.keys(air.failures).length > 0 && (
        <Callout tone="bad" className="mb-2">
          <b>{Object.keys(air.failures).sort().join(', ')} returned no reading.</b> That terminal
          falls back to soil alone; no value has been substituted.
          {Object.entries(air.failures).map(([name, reason]) => (
            <div key={name} className="num mt-1 text-[10.5px] opacity-80">
              {name} — {reason}
            </div>
          ))}
        </Callout>
      )}

      {air && (
        <>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            {air.stations.map((station) => (
              <Readout
                key={station.name}
                label={station.name}
                value={num(station.tempC, 1)}
                unit={station.tempC != null ? '°C' : undefined}
                accent={station.tempC != null ? C.gold : C.red}
                sub={station.tempC != null ? 'in T_env' : 'no reading'}
              />
            ))}
          </div>
          <p className="num mt-2 text-[10.5px] text-muted">
            {air.provider} · fetched {air.ageSeconds.toFixed(0)} s ago · air enters T_env at these
            two terminals only; the buried run uses soil alone
          </p>
        </>
      )}

      {runError && (
        <Callout tone="bad" className="mt-3">
          <b>That run failed.</b> The results shown are from the last successful run.{' '}
          <code className="num text-[11px]">{runError}</code>
        </Callout>
      )}

      {result && (
        <div
          className="transition-opacity duration-200"
          style={{ opacity: pending ? 0.55 : 1 }}
          aria-busy={pending}
        >
          {!result.physics.feasible && result.physics.warnings.length > 0 && (
            <Callout tone="bad" className="mt-4">
              <b>Hydraulically infeasible.</b> {result.physics.warnings[0]}
            </Callout>
          )}

          {/* ── KPI row ────────────────────────────────────────── */}
          <div className="mt-4 grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-6">
            <Kpi
              label="Receipt temperature"
              value={num(result.kpi.receiptTempC, 1)}
              unit="°C"
              delta={`${num(result.kpi.deltaTempC, 1, { sign: true })} °C over ${meta.route.lengthKm.toFixed(0)} km`}
              direction={result.kpi.deltaTempC < 0 ? 'down' : 'up'}
              accent={C.gold}
            />
            <Kpi
              label="Environment at receipt"
              value={num(result.kpi.receiptEnvC, 1)}
              unit="°C"
              delta={
                result.kpi.receiptAirC != null
                  ? `(air ${num(result.kpi.receiptAirC, 1)} + soil ${num(result.kpi.receiptSoilC, 1)}) / 2`
                  : `soil ${num(result.kpi.receiptSoilC, 1)} °C only — no live air`
              }
              accent={C.cyan}
            />
            <Kpi
              label="Receipt pressure"
              value={num(result.kpi.receiptPressureBar, 1)}
              unit="bar g"
              delta={`${num(result.kpi.deltaPressureBar, 1, { sign: true })} bar`}
              direction={result.kpi.deltaPressureBar < 0 ? 'down' : 'up'}
              accent={result.physics.feasible ? C.blue : C.red}
            />
            <Kpi
              label="Gross volume"
              value={num(result.kpi.grossVolumeKL, 1, { group: true })}
              unit="KL"
              delta={`${num(result.kpi.deltaGrossKL, 1, { sign: true })} KL (${num(result.kpi.deltaGrossPct, 2, { sign: true })} %)`}
              direction={result.kpi.deltaGrossKL < 0 ? 'down' : 'up'}
              accent={C.gold}
            />
            <Kpi
              label="Standard volume @ 15 °C"
              value={num(result.kpi.stdVolumeKL, 2, { group: true })}
              unit="KL"
              delta={`drift ${result.kpi.stdVolumeDriftPct.toExponential(1)} %`}
              direction="flat"
              accent={C.green}
            />
            <Kpi
              label="Transit time"
              value={num(result.kpi.transitHours, 1)}
              unit="hr"
              delta={`${num(result.kpi.meanVelocityMs, 2)} m/s mean`}
              accent={C.violet}
            />
          </div>

          {/* ── Mimic ──────────────────────────────────────────── */}
          <Section title="Pipeline mimic" note="pipe colour = product temperature" />
          <Mimic result={result} meta={meta} />

          {result.kpi.stdVolumeDriftPct >= 0.01 && (
            <Callout tone="bad" className="mt-3">
              <b>Standard volume drifted {result.kpi.stdVolumeDriftPct.toExponential(2)} %.</b> The
              VCF chain is not internally consistent — this should never happen.
            </Callout>
          )}

          {/* ── Detail tabs ────────────────────────────────────── */}
          <div className="mt-6">
            <Tabs tabs={TABS} active={tab} onChange={setTab} />
            <div className="pt-4">
              {tab === 'thermal' && <ThermalCharts result={result} meta={meta} />}
              {tab === 'hydraulics' && <HydraulicCharts result={result} />}
              {tab === 'stations' && (
                <>
                  <StationTable
                    result={result}
                    meta={meta}
                    onExport={handleExport}
                    exporting={exporting}
                  />
                  {exportError && (
                    <Callout tone="bad" className="mt-3">
                      <b>Export failed.</b>{' '}
                      <code className="num text-[11px]">{exportError}</code>
                    </Callout>
                  )}
                </>
              )}
              {tab === 'route' && (
                <Suspense fallback={<TabSkeleton label="Loading the basemap…" />}>
                  <RouteMap result={result} meta={meta} />
                </Suspense>
              )}
              {tab === 'model' && (
                <Suspense fallback={<TabSkeleton label="Loading…" />}>
                  <ModelNotes result={result} meta={meta} />
                </Suspense>
              )}
            </div>
          </div>
        </div>
      )}

      <footer className="mt-12 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-edge pt-5 text-[10.5px] text-muted">
        <span>
          BPCL / MMBL Bina–Bijwasan · physics-based simulation, no machine learning in the solution
          path
        </span>
        <span className="num">
          API MPMS 11.1 / 11.2.1 · Johansen 1975 · Colebrook–White · ERA5-Land
        </span>
      </footer>
    </main>
  );
}

function TabSkeleton({ label }: { label: string }) {
  return (
    <div className="panel animate-pulse-soft flex h-72 items-center justify-center text-[12px] text-muted">
      {label}
    </div>
  );
}
