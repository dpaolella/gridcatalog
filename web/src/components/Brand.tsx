/**
 * Brand furniture, in one file.
 *
 * Every reference to the lockup, the mark, the hexagon motif and the line-icons
 * goes through here, so the identity is changed in one place rather than found
 * in twenty.
 */

/**
 * The lockup: the mark, then the wordmark set in Inter.
 *
 * Composed rather than loaded as a single SVG, and that is a deliberate
 * reversal. The first version was an `<img>` pointing at a lockup file — which
 * is what the brand bundle ships and what a designer expects to hand over. It
 * rendered wrong, and the reason is worth writing down: an SVG loaded through
 * `<img>` is an isolated document. It cannot see the page's `@font-face`
 * rules, so its `<text>` fell back to whatever the system had, at metrics the
 * file's viewBox was not drawn for, and the wordmark was clipped mid-letter.
 *
 * A lockup with live text only works when the text is outlines. Until the
 * outlined artwork is here, setting the wordmark in the Inter this page has
 * already loaded is both more correct and more honest: it is the right
 * typeface at the right weight, it takes its colour from its context, and it
 * is crisp at any size.
 *
 * To use the official file once it exists: render it here as an `<img>`, in
 * this component, and nothing else in the UI changes.
 */
export function Logo({
  variant,
  className = "h-7",
}: {
  /** Force the wordmark's colour. Only for a lockup sitting on a dark panel
   *  inside a light page — the footer. Left unset it follows the page, which
   *  is the brand rule (petrol on light, white on dark) and the only version
   *  that survives a reader in dark mode. */
  variant?: "light" | "dark";
  className?: string;
}) {
  const color =
    variant === "dark"
      ? "#ffffff"
      : variant === "light"
        ? "var(--og-petrol)"
        : "var(--foreground)";
  return (
    <span className={`inline-flex items-center gap-2 ${className}`} aria-label="OpenGrid">
      <Mark className="h-full w-auto text-[color:var(--og-orange)]" />
      <span
        aria-hidden
        className="text-[1.05em] font-semibold leading-none tracking-tight"
        style={{ color }}
      >
        OpenGrid
      </span>
    </span>
  );
}

/**
 * The mark alone, inheriting `currentColor`.
 *
 * Inlined rather than an `<img>`: the mark is used at small sizes inside text
 * colour contexts (the footer, the panel), and a raster or an external SVG
 * cannot follow the colour of what it sits in.
 */
export function Mark({ className = "h-6 w-auto" }: { className?: string }) {
  return (
    <svg viewBox="0 0 520 420" className={className} role="img" aria-label="OpenGrid">
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth={34}
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M180 27h248a40 40 0 0 1 35 20l124 163a40 40 0 0 1 0 40L463 373a40 40 0 0 1-35 20H180a40 40 0 0 1-35-20L21 250a40 40 0 0 1 0-40L145 47a40 40 0 0 1 35-20Z" />
        <path d="M242 105h146a28 28 0 0 1 24 14l73 96a28 28 0 0 1 0 28l-73 96a28 28 0 0 1-24 14H242a28 28 0 0 1-24-14l-73-96a28 28 0 0 1 0-28l73-96a28 28 0 0 1 24-14Z" />
        <path d="M180 27 411 393" />
      </g>
    </svg>
  );
}

/**
 * The divider rule. Signal Blue on light, Teal on dark, set by the token.
 *
 * A component rather than a raw `<hr>` so that "there is a rule under the
 * title" is a decision made once. It is a confirmed brand device and general
 * design advice to remove it does not apply.
 */
export function Rule({ className = "" }: { className?: string }) {
  return <hr className={`og-rule ${className}`} />;
}

/**
 * The hexagon circuit motif as a corner or panel wash.
 *
 * Low strength, masked away from the text side, at an edge. It is the defining
 * brand motif and also the easiest thing here to overdo: behind a heading or
 * along a panel, never across a page of body copy.
 */
export function HexWash({
  className = "",
  color = "currentColor",
  opacity = 0.09,
}: {
  className?: string;
  color?: string;
  opacity?: number;
}) {
  const id = washId(color, opacity);
  return (
    <svg
      className={`pointer-events-none absolute inset-0 h-full w-full ${className}`}
      aria-hidden
      focusable="false"
      style={{ opacity }}
    >
      <defs>
        <pattern id={`${id}-hex`} width={120} height={104} patternUnits="userSpaceOnUse">
          <g fill="none" stroke={color} strokeWidth={1.5}>
            <path d="M30 0 90 0 120 52 90 104 30 104 0 52Z" />
            <path d="M90 -52 150 -52 180 0 150 52 90 52 60 0Z" />
            <path d="M90 52 150 52 180 104 150 156 90 156 60 104Z" />
          </g>
        </pattern>
        <linearGradient id={`${id}-fade`} x1="1" y1="0" x2="0" y2="0">
          <stop offset="0%" stopColor="#fff" stopOpacity="1" />
          <stop offset="70%" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <mask id={`${id}-mask`}>
          <rect width="100%" height="100%" fill={`url(#${id}-fade)`} />
        </mask>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${id}-hex)`} mask={`url(#${id}-mask)`} />
    </svg>
  );
}

/**
 * A `<defs>` id that is unique per *appearance*, not per instance.
 *
 * SVG ids are document-global, and `url(#name)` resolves to the first match in
 * the document. Three washes on one page — header, hero, footer — all declaring
 * `id="og-hex-wash"` meant the footer's white pattern was defined and then
 * ignored: every reference found the header's petrol one, and the footer's
 * hexagons rendered petrol on petrol, which is to say not at all.
 *
 * Keying on the parameters rather than on a per-render counter is deliberate.
 * Two washes that look the same *are* the same, so a collision between them
 * changes nothing; and the id is a pure function of the props, so the server's
 * HTML and the client's first render agree, which a counter or `useId` in a
 * component that renders in both places would have to be careful about.
 */
function washId(color: string, opacity: number): string {
  const key = `${color}|${opacity}`;
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) | 0;
  return `og-wash-${(hash >>> 0).toString(36)}`;
}
