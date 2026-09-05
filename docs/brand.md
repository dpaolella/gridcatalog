# The OpenGrid identity in the web UI

The UI is built to the OpenGrid visual system: orange and petrol, Inter, the
hexagon circuit motif, squared geometry. This file records how that system maps
onto a data catalog — the parts where a brand written for slides had to be
decided for an interface — and what to change when the official artwork
arrives.

## The artwork is a reconstruction

The mark and the hexagon motif are drawn from the brand images, not from the
official files. Both live in `web/src/components/Brand.tsx` — `Logo`, `Mark`,
`Rule`, `HexWash` — plus `web/src/app/icon.svg` for the favicon. That is the
only place to change when the official artwork arrives.

The brand's four line-icons (lightbulb, cycle, transmission tower, wind
turbines) are **not used here**, and that is a decision rather than an
omission: nothing in this interface is clearer as a picture than as its name,
and the brand's own instruction is that nothing is added for decoration. If a
glyph ever does earn its place, draw it inline at a 2px stroke in the same
geometry — do not install an icon library alongside a four-icon brand set.

**The lockup is composed, not a file**, and the reason is worth knowing before
anyone "fixes" it. The first version was an `<img>` pointing at a lockup SVG,
which is what a brand bundle ships. It rendered wrong: an SVG loaded through
`<img>` is an isolated document that cannot see the page's `@font-face` rules,
so its `<text>` fell back to a system font at metrics the viewBox was not drawn
for, and the wordmark was clipped mid-letter. A lockup with live text only
works when the text is outlines.

So `Logo` draws the mark inline and sets "OpenGrid" in the Inter the page has
already loaded. It is the right typeface at the right weight, it follows the
page's foreground colour — petrol on light, white on dark, which is the brand
rule — and it is crisp at any size.

To use `assets/logo/logo on white.svg` and `logo on dark.svg` once they are to
hand: render them as an `<img>` inside `Logo`, and nothing else in the UI
changes.

## The palette, and where it is allowed

`web/src/app/globals.css` carries the exact theme colours as `--og-*` custom
properties, then derives semantic tokens from them. Components use the semantic
tokens; only the token definitions name a colour.

The rule that shapes most of the UI is **orange is a spark, not a wash**. It
marks the one thing on a view that matters most — a CTA, a bounding box on a
coverage map, the correlated-connection rail, the reference-only tag — and
never fills a content background. Petrol is the dominant dark, carrying the
footer, the panels and the statement moments. Signal Blue and Teal are line
colours: the divider rule, and the icon strokes. They are not fills.

## Quality grades are a petrol ramp, not a traffic light

The one place where the obvious design would have been wrong.

Three independent facets — provenance, documentation, currency — each get a
grade from A to D. Colouring them red-to-green would make them *look*
averageable, and the PRD forbids a composite score for a good reason: a dataset
with impeccable provenance and no documentation is not "a C". A reader who sees
two greens and a red will compute the average anyway, because that is what a
traffic light is for.

So the ramp is a single petrol scale, dark for A and pale for D, with no hue
change at all. It also says the truer thing: these facets measure how much is
*established* about a dataset, not how good the dataset is. A D in currency
means nobody has checked lately, not that the data is stale.

Status colours — teal for healthy, orange for an alert — are used for link
health, where a state genuinely is good or bad.

## Type

Inter, self-hosted through `next/font`, at Light 300 for body, SemiBold 600 for
headings and Black 900 reserved for statement numbers. Sentence case
throughout; body copy left-aligned. A monospace (IBM Plex Mono) carries
identifiers, WKT and code — it is not part of the brand type system, so it
stays quiet and appears only where character alignment carries meaning.

## The devices are deliberate

The thin divider rule under a title, the colour panels, and the hexagon bands
are confirmed OpenGrid devices, not decoration that crept in. General "good
design" advice says to strip a rule under a heading. Do not strip these.

`Brand.tsx` exposes them as `Rule`, `HexWash`, and the `.og-panel`, `.og-cta`,
`.og-card`, `.og-stat`, `.og-tag` and `.og-eyebrow` classes.

## What not to do

No off-brand colours — use the tokens. No orange as a large content
background. No centred body text. No pill-rounded or heavy-shadow cards; the
radius is 2px and the CTA at 3px is the only rounded thing. No full-page photos
behind text. No typeface substitution. No third-party icon library alongside
the four brand glyphs — match their weight and geometry for any new one.
