/** Same-origin viewer assets, which may use a display-resolution document
 * distinct from the registered download. */
export const MAPPED_MODELS: Record<string, { dir: string; document: string }> = {
  "gb-osm-reference": { dir: "gb-osm", document: "system.view.json" },
  "de-osm-reference": { dir: "de-osm", document: "system.view.json" },
  "kpg-193-reference": { dir: "kpg-193", document: "system.view.json" },
};
