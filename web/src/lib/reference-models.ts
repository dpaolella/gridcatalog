/** Same-origin viewer assets, which may use a display-resolution document
 * distinct from the registered download. */
export const MAPPED_MODELS: Record<string, { dir: string; document: string }> = {
  "cascade-interconnect-reference": { dir: "cascade-interconnect", document: "system.json" },
  "gb-osm-reference": { dir: "gb-osm", document: "system.view.json" },
};
