export default function AboutPage() {
  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">About</h1>
      <p>
        The OpenGrid Data Hub is a discovery and routing layer for grid-modelling data. It holds
        metadata about datasets and issues access plans pointing at where the bytes actually live.
        It is never in the byte path.
      </p>
      <section className="space-y-3">
        <h2 className="font-medium">What it will not do</h2>
        <ul className="space-y-2 text-sm text-[color:var(--muted)]">
          <li>
            <strong className="text-[color:var(--foreground)]">Fill in a blank.</strong> A field
            absent from a record means it has not been catalogued — never that the dataset lacks it.
            The completeness level says how far a record has got.
          </li>
          <li>
            <strong className="text-[color:var(--foreground)]">Combine the quality facets.</strong>{" "}
            Provenance, documentation and currency are graded independently and never averaged. A
            dataset can be perfectly current and completely unprovenanced, and one number would hide
            exactly that.
          </li>
          <li>
            <strong className="text-[color:var(--foreground)]">Rank datasets against each other.</strong>{" "}
            Every grade derives from recorded facts about that record alone. There is no leaderboard.
          </li>
          <li>
            <strong className="text-[color:var(--foreground)]">Arbitrate an allow-list.</strong> A
            restricted dataset&rsquo;s custodian decides who may see it. OpenGrid stores and enforces
            that list and never edits it.
          </li>
        </ul>
      </section>
    </div>
  );
}
