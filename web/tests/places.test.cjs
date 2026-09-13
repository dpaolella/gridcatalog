const { test } = require("node:test");
const assert = require("node:assert/strict");
const load = require("./load-ts.cjs");
const { groupByPlace, selectedPlace, placeToken } = load("places.ts");

const placed = (id, iri, label) => ({
  id, title: id, data_domains: [], quality: [], completeness_level: 2,
  spatial: { place_iris: [iri], place_labels: [label] },
});
const GB = "https://schema.opengrid.org/concept/place/greatBritain";
const DE = "https://schema.opengrid.org/concept/place/germany";

test("a place IRI reduces to the token a shared link carries", () => {
  assert.equal(placeToken(GB), "greatBritain");
  assert.equal(placeToken(DE), "germany");
});

test("models group by place IRI, and the picker reads in label order", () => {
  const { places, unplaced } = groupByPlace([placed("gb", GB, "Great Britain"), placed("de", DE, "Germany")]);
  assert.deepEqual(places.map((p) => p.token), ["germany", "greatBritain"]);
  assert.deepEqual(places.map((p) => p.label), ["Germany", "Great Britain"]);
  assert.equal(unplaced.length, 0);
});

test("two models of one place are one entry, not two", () => {
  const { places } = groupByPlace([placed("gb-400", GB, "Great Britain"), placed("gb-275", GB, "Great Britain")]);
  assert.equal(places.length, 1);
  assert.deepEqual(places[0].models.map((m) => m.id), ["gb-400", "gb-275"]);
});

test("one place is not split by disagreeing labels", () => {
  // The reason the key is the IRI. Three strings, one place; a label-keyed
  // picker would offer the reader a choice between synonyms.
  const { places } = groupByPlace([placed("a", GB, "Great Britain"), placed("b", GB, "GB"), placed("c", GB, "Britain")]);
  assert.equal(places.length, 1);
  assert.equal(places[0].models.length, 3);
});

test("a model with no place is listed, not filed under a guess", () => {
  const nowhere = { id: "x", title: "x", data_domains: [], quality: [], completeness_level: 1 };
  const { places, unplaced } = groupByPlace([placed("de", DE, "Germany"), nowhere]);
  assert.deepEqual(places.map((p) => p.token), ["germany"]);
  assert.deepEqual(unplaced.map((m) => m.id), ["x"]);
});

test("an unknown token widens the view instead of emptying it", () => {
  const { places } = groupByPlace([placed("de", DE, "Germany")]);
  assert.equal(selectedPlace(places, "germany"), "germany");
  for (const token of ["atlantis", "", null]) assert.equal(selectedPlace(places, token), null);
});
