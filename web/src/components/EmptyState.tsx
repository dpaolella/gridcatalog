import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Every empty state is designed (PRD §F3: "empty states matter here … design
 * each explicitly").
 *
 * The rule they all follow: say what happened, not what is missing. "No
 * fields" reads as "this dataset has no columns", which is almost never true;
 * what is true is that nobody has catalogued them yet, and the record's
 * completeness level says how far it has got.
 */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: { href: string; label: string };
}) {
  return (
    <div
      className="rounded-lg border border-dashed p-8 text-center"
      style={{ borderColor: "var(--border)" }}
    >
      <p className="text-base font-medium">{title}</p>
      {children ? (
        <div className="mx-auto mt-2 max-w-prose text-sm text-[color:var(--muted)]">{children}</div>
      ) : null}
      {action ? (
        <Link
          href={action.href}
          className="mt-4 inline-block rounded border px-3 py-1.5 text-sm hover:bg-[color:var(--accent-soft)]"
          style={{ borderColor: "var(--border)" }}
        >
          {action.label}
        </Link>
      ) : null}
    </div>
  );
}

/**
 * A value the catalog does not hold.
 *
 * Rendered as words rather than as an em dash or a blank cell, because both of
 * those read as "this dataset does not have one" — and PRD principle 2 says
 * absent means *not captured*, never "no source".
 */
export function NotCaptured({ hint }: { hint?: string }) {
  return (
    <span className="text-sm italic text-[color:var(--muted)]" title={hint}>
      Not captured
    </span>
  );
}
