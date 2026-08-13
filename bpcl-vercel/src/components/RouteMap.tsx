/**
 * Route map
 * =========
 *
 * The centreline on a dark basemap, coloured by product temperature, with the
 * station markers on top and the route CSV underneath.
 *
 * The polyline is split into ~90 short segments so it can carry the same
 * temperature colour ramp as the mimic diagram. Leaflet has no gradient
 * polyline, and a single-colour route here would throw away the one variable
 * the map could usefully add to a chart that already has chainage on the x-axis:
 * where on the ground the product is cold.
 *
 * Tiles are CARTO's dark_matter, which needs no API key or token. That keeps the
 * deployment free of another secret to manage.
 */

import { useMemo } from 'react';
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Tooltip } from 'react-leaflet';
import type { Meta, SimResult } from '../api';
import { C, sampleTempScale } from '../theme';
import { Section, num } from './ui';
import 'leaflet/dist/leaflet.css';

const SEGMENTS = 90;

export function RouteMap({ result, meta }: { result: SimResult; meta: Meta }) {
  const { profile } = result;

  const { segments, centre, bounds } = useMemo(() => {
    const lats = profile.lat;
    const lons = profile.lon;
    const temps = profile.T_C;

    const lo = Math.min(...temps) - 0.5;
    const hi = Math.max(...temps) + 0.5;
    const span = Math.max(hi - lo, 1e-6);

    const step = Math.max(1, Math.floor(lats.length / SEGMENTS));
    const out: { path: [number, number][]; colour: string; t: number; km: number }[] = [];
    for (let i = 0; i < lats.length - 1; i += step) {
      const end = Math.min(i + step, lats.length - 1);
      const path: [number, number][] = [];
      for (let j = i; j <= end; j += 1) path.push([lats[j], lons[j]]);
      const mid = Math.floor((i + end) / 2);
      out.push({
        path,
        colour: sampleTempScale((temps[mid] - lo) / span),
        t: temps[mid],
        km: profile.km[mid],
      });
    }

    return {
      segments: out,
      centre: [
        lats.reduce((a, b) => a + b, 0) / lats.length,
        lons.reduce((a, b) => a + b, 0) / lons.length,
      ] as [number, number],
      bounds: [
        [Math.min(...lats), Math.min(...lons)],
        [Math.max(...lats), Math.max(...lons)],
      ] as [[number, number], [number, number]],
    };
  }, [profile]);

  return (
    <div>
      {/* The route is tall and narrow — 4.4° of latitude against 1.5° of
          longitude — so fitting it in a full-width panel is height-limited and
          leaves most of the width empty. The station strip beside it uses that
          space for something worth reading instead. */}
      <div className="grid gap-3 xl:grid-cols-[1fr_300px]">
      <div className="panel overflow-hidden">
        <MapContainer
          bounds={bounds}
          boundsOptions={{ padding: [26, 26] }}
          scrollWheelZoom={false}
          style={{ height: 560, width: '100%', background: '#0a0f1a' }}
        >
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
            maxZoom={19}
          />

          {/* A dark casing under the coloured run, so the line stays legible
              where it crosses a pale patch of basemap. */}
          <Polyline
            positions={profile.lat.map((lat, i) => [lat, profile.lon[i]] as [number, number])}
            pathOptions={{ color: '#000', weight: 8, opacity: 0.55 }}
          />
          {segments.map((segment, i) => (
            <Polyline key={i} positions={segment.path} pathOptions={{ color: segment.colour, weight: 4.5 }}>
              <Tooltip sticky>
                <span className="num">
                  km {num(segment.km, 0)} · {num(segment.t, 1)} °C
                </span>
              </Tooltip>
            </Polyline>
          ))}

          {result.stations.map((station) => {
            const terminal = station.T_air_C != null;
            return (
              <CircleMarker
                key={station.waypoint_name}
                center={[
                  profile.lat[profile.km.indexOf(station.km)] ?? centre[0],
                  profile.lon[profile.km.indexOf(station.km)] ?? centre[1],
                ]}
                radius={terminal ? 8 : 6}
                pathOptions={{
                  color: '#080C15',
                  weight: 2,
                  fillColor: terminal ? C.gold : C.cyan,
                  fillOpacity: 1,
                }}
              >
                <Popup>
                  <div className="min-w-40">
                    <div className="mb-1.5 text-[12px] font-bold" style={{ color: C.gold }}>
                      {station.waypoint_name}
                    </div>
                    <PopupRow label="Chainage" value={`${num(station.km, 0)} km`} />
                    <PopupRow label="Elevation" value={`${num(station.elevation_m, 0)} m`} />
                    <PopupRow label="Product" value={`${num(station.T_C, 2)} °C`} colour={C.gold} />
                    <PopupRow
                      label="Environment"
                      value={`${num(station.T_env_C, 2)} °C`}
                      colour={C.cyan}
                    />
                    {station.T_air_C != null && (
                      <PopupRow
                        label="Air (live)"
                        value={`${num(station.T_air_C, 2)} °C`}
                        colour={C.amber}
                      />
                    )}
                    <PopupRow
                      label="Pressure"
                      value={`${num(station.P_bar, 2)} bar`}
                      colour={C.blue}
                    />
                  </div>
                </Popup>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </div>

        {/* Station strip — the same nine points as the map, read top to bottom
            in chainage order, so the thermal decay down the line is legible
            without hovering anything. */}
        <div className="panel flex flex-col overflow-hidden">
          <div className="rail-label border-b border-edge px-3.5 py-2.5">
            Along the line · {meta.route.lengthKm.toFixed(0)} km
          </div>
          <div className="flex-1 overflow-y-auto">
            {result.stations.map((station, i) => {
              const terminal = station.T_air_C != null;
              const previous = i > 0 ? result.stations[i - 1].T_C : null;
              return (
                <div
                  key={station.waypoint_name}
                  className="flex items-center gap-2.5 border-b border-edge/35 px-3.5 py-2.5 last:border-0"
                >
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: terminal ? C.gold : C.cyan }}
                    aria-hidden
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[11.5px] font-semibold text-ink">
                      {station.waypoint_name.split(' (')[0]}
                    </div>
                    <div className="num text-[9.5px] text-muted">
                      km {num(station.km, 0)} · {num(station.elevation_m, 0)} m ·{' '}
                      {num(station.P_bar, 1)} bar
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="num text-[13.5px] font-bold" style={{ color: C.gold }}>
                      {num(station.T_C, 1)}
                      <span className="ml-0.5 text-[9px] font-normal text-muted">°C</span>
                    </div>
                    {previous != null && (
                      <div className="num text-[9px] text-muted">
                        {num(station.T_C - previous, 2, { sign: true })}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <Section title="Route definition" note="geometry is data — the whole route is one CSV" />
      <div className="panel overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-edge">
              {['#', 'Name', 'Lat', 'Lon', 'km', 'Elev [m]', 'OD [in]', 'Wall [mm]', 'Burial [m]', 'Type', 'Source'].map(
                (head) => (
                  <th
                    key={head}
                    className="bg-panel px-2.5 py-2.5 text-[9.5px] font-semibold tracking-[0.08em] text-muted uppercase whitespace-nowrap"
                  >
                    {head}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {meta.route.waypoints.map((wp) => (
              <tr
                key={wp.id}
                className="border-b border-edge/40 transition-colors last:border-0 hover:bg-panel-2"
              >
                <td className="num px-2.5 py-1.5 text-[11.5px] text-muted">{wp.id}</td>
                <td className="px-2.5 py-1.5 text-[11.5px] font-semibold whitespace-nowrap text-ink">
                  {wp.name}
                </td>
                <td className="num px-2.5 py-1.5 text-[11.5px]">{num(wp.lat, 4)}</td>
                <td className="num px-2.5 py-1.5 text-[11.5px]">{num(wp.lon, 4)}</td>
                <td className="num px-2.5 py-1.5 text-[11.5px]">{num(wp.km, 1)}</td>
                <td className="num px-2.5 py-1.5 text-[11.5px]">{num(wp.elevationM, 0)}</td>
                <td
                  className="num px-2.5 py-1.5 text-[11.5px] font-semibold"
                  style={{ color: wp.odInch < 12 ? C.violet : C.text }}
                >
                  {num(wp.odInch, 3)}
                </td>
                <td className="num px-2.5 py-1.5 text-[11.5px]">{num(wp.wallMm, 2)}</td>
                <td className="num px-2.5 py-1.5 text-[11.5px]">{num(wp.burialM, 1)}</td>
                <td className="px-2.5 py-1.5 text-[11px] whitespace-nowrap text-muted">{wp.type}</td>
                <td className="num px-2.5 py-1.5 text-[10px] whitespace-nowrap text-muted">
                  {wp.source}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PopupRow({ label, value, colour }: { label: string; value: string; colour?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-[11px]">
      <span style={{ color: C.muted }}>{label}</span>
      <span className="num font-semibold" style={{ color: colour ?? C.text }}>
        {value}
      </span>
    </div>
  );
}
