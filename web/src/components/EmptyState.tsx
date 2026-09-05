import Link from "next/link";
import type { ReactNode } from "react";
import { HexWash, Rule } from "@/components/Brand";

/**
 * Every empty state is designed.
 *
 * The rule they all follow: say what happened, not what is missing. "No fields"
 * reads as "this dataset has no columns", which is almost never true; what is
 * true is that nobody has catalogued them yet, and the completeness level says
 * how far the record has got.
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
    <div className="og-card relative overflow-hidden px-8 py-12">
      <HexWash color="var(--og-petrol)" opacity={0.06} />
      <div className="relative max-w-prose">
        <p className="text-lg font-semibold">{title}</p>
        <Rule />
        {children ? (
          <div className="mt-4 text-sm text-[color:var(--muted)]">{children}</div>
        ) : null}
        {action ? (
          <Link href={action.href} className="og-cta mt-6">
            {action.label}
          </Link>
        ) : null}
      </div>
    </div>
  );
}

/**
 * A value the catalog does not hold.
 *
 * Words rather than an em dash or a blank cell, because both of those read as
 * "this dataset does not have one" — and absent means *not captured*, never
 * "no source".
 */
export function NotCaptured({ hint }: { hint?: string }) {
  return (
    <span className="text-sm italic text-[color:var(--muted)]" title={hint}>
      Not captured
    </span>
  );
}
