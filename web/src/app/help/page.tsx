import Link from "next/link";

export default function HelpPage() {
  const items = [
    {
      q: "Why does this dataset have no schema tab?",
      a: "Nobody has catalogued its fields yet. The record is at completeness level 1 — enough to find it and get at it, not enough to describe what is inside. The tab says so rather than showing an empty table.",
    },
    {
      q: "Why is a facet showing 'not yet assessed' instead of a grade?",
      a: "Provenance and documentation are graded from field-level metadata, which a level 1 record does not carry. Not assessed is not a poor grade, and showing D would condemn every record for having been harvested rather than hand-curated.",
    },
    {
      q: "Why is there no overall quality score?",
      a: "Because averaging the three facets destroys the only information you could act on. A dataset that is current, well documented and untraceable is a different problem from one that is traceable, well documented and five years stale, and one number makes them look the same.",
    },
    {
      q: "What does the triangle on a connection mean?",
      a: "The two datasets share an upstream source. Their agreement is partly that source agreeing with itself, so treating them as corroborating evidence understates uncertainty. The pairing is still shown, with its strength reduced — hiding it would leave you believing they are independent.",
    },
    {
      q: "I searched for a dataset I know exists and got nothing.",
      a: "Either it is not catalogued yet — in which case please submit it — or it is restricted and its existence is not public. The catalog answers both the same way on purpose: a different answer would tell you which.",
    },
    {
      q: "Can I download data from here?",
      a: "No. OpenGrid redirects you to the source and never proxies bytes. For a partial read of a large dataset, use the Python SDK: it fetches an access plan and reads only the slice you asked for.",
    },
  ];

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Help</h1>
      <dl className="space-y-5">
        {items.map((item) => (
          <div key={item.q}>
            <dt className="font-medium">{item.q}</dt>
            <dd className="mt-1 text-sm text-[color:var(--muted)]">{item.a}</dd>
          </div>
        ))}
      </dl>
      <p className="text-sm">
        Still stuck?{" "}
        <Link href="/developers" className="text-[color:var(--accent)] hover:underline">
          Developer documentation
        </Link>
        .
      </p>
    </div>
  );
}
