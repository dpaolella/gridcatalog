import { bboxToWkt, formatSpan, isGlobal } from "@/lib/format";

/**
 * Coverage, drawn rather than described.
 *
 * Inline SVG and no map library: a world outline as a path is a few hundred
 * bytes and works offline, while a tile-based map is a third-party request on
 * every catalog row and a dependency on somebody else's uptime for a picture
 * that only has to say "roughly here".
 *
 * The projection is equirectangular, which is wrong for area and right for
 * this: a bounding box is an axis-aligned rectangle in lon/lat, and any
 * projection that curved it would draw a shape the data does not have.
 */
export function CoverageMap({
  bbox,
  className = "",
}: {
  bbox?: number[] | null;
  className?: string;
}) {
  const W = 360;
  const H = 180;
  const box = bbox && bbox.length === 4 ? bbox : null;
  const x = (lon: number) => lon + 180;
  const y = (lat: number) => 90 - lat;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className={className}
      role="img"
      aria-label={
        box
          ? `Coverage from ${box[1]}° to ${box[3]}° latitude and ${box[0]}° to ${box[2]}° longitude`
          : "Coverage not captured"
      }
      style={{ background: "var(--surface)" }}
    >
      {/* Graticule every 30°, so a reader can place the box without a
          coastline. Cheaper and more honest than a low-resolution outline
          that would be recognisably wrong at this size. */}
      <g stroke="var(--border)" strokeWidth="0.5" fill="none">
        {[-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150].map((lon) => (
          <line key={lon} x1={x(lon)} y1={0} x2={x(lon)} y2={H} />
        ))}
        {[-60, -30, 0, 30, 60].map((lat) => (
          <line key={lat} x1={0} y1={y(lat)} x2={W} y2={y(lat)} />
        ))}
      </g>
      <rect x={0} y={0} width={W} height={H} fill="none" stroke="var(--border)" strokeWidth="1" />
      {box ? (
        <rect
          x={x(box[0])}
          y={y(box[3])}
          width={Math.max(x(box[2]) - x(box[0]), 1.5)}
          height={Math.max(y(box[1]) - y(box[3]), 1.5)}
          fill="var(--accent)"
          fillOpacity={0.25}
          stroke="var(--accent)"
          strokeWidth="1.5"
        />
      ) : null}
    </svg>
  );
}

/**
 * A coverage window as a bar on a fixed axis.
 *
 * Fixed rather than scaled to the dataset, so two records compare by eye. A
 * timeline that fitted each dataset to its own width would make a one-year
 * snapshot and a forty-year reanalysis look identical, which is the opposite
 * of what the picture is for.
 */
export function CoverageTimeline({
  start,
  end,
  from = 1980,
  to = new Date().getUTCFullYear() + 1,
}: {
  start?: string | null;
  end?: string | null;
  from?: number;
  to?: number;
}) {
  const span = formatSpan(start, end);
  if (!span) return null;

  const year = (value?: string | null) => {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed.getUTCFullYear();
  };
  const s = Math.max(year(start) ?? from, from);
  const e = Math.min(year(end) ?? to, to);
  const scale = (value: number) => ((value - from) / (to - from)) * 100;

  return (
    <div className="w-full" aria-label={`Coverage ${span}`}>
      <div
        className="relative h-1.5 w-full rounded-full"
        style={{ background: "var(--border)" }}
        role="img"
      >
        <div
          className="absolute h-1.5 rounded-full"
          style={{
            left: `${scale(s)}%`,
            width: `${Math.max(scale(e) - scale(s), 1)}%`,
            background: "var(--accent)",
          }}
        />
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-[color:var(--muted)]">
        <span>{from}</span>
        <span className="font-medium text-[color:var(--foreground)]">{span}</span>
        <span>{to}</span>
      </div>
    </div>
  );
}

export function BboxSummary({ bbox }: { bbox?: number[] | null }) {
  if (!bbox || bbox.length !== 4) return null;
  if (isGlobal(bbox)) return <span>Global</span>;
  const [minLon, minLat, maxLon, maxLat] = bbox;
  return (
    <span className="font-mono text-xs">
      {minLat.toFixed(2)}° – {maxLat.toFixed(2)}° N, {minLon.toFixed(2)}° – {maxLon.toFixed(2)}° E
    </span>
  );
}

export { bboxToWkt };
