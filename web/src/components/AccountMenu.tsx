"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

/** `<Link>` and the router get the base path applied for them; a raw `fetch`
 *  does not. `NEXT_PUBLIC_` is inlined into the browser bundle at build time,
 *  which is what makes it readable here. */
const SESSION_ENDPOINT = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/session`;

/**
 * Who is signed in, in the header.
 *
 * A client component, and deliberately: the alternative is reading the session
 * cookie in the root layout, which tells Next that *every* route depends on the
 * request and throws away the incremental caching that makes a catalog page
 * fast. The identity of the reader is the one thing on the page that varies per
 * reader, so it is the one thing fetched per reader.
 *
 * It calls this site's own `/api/session`, not the API. The session cookie is
 * `HttpOnly` and belongs to the API's host: the server forwards it, the browser
 * never sees it, and no CORS allowance is needed for whatever topology a
 * deployment uses.
 *
 * Renders nothing at all until it knows. A control that says "Sign in" and then
 * changes to a name is worse than one that appears a moment late — the first
 * invites a signed-in steward to sign in again.
 */
type Session = {
  authenticated: boolean;
  email?: string | null;
  is_steward?: boolean;
  /** The API did not answer, so `authenticated: false` is a placeholder rather
   *  than a finding. Set by `/api/session`, and it was ignored here: a
   *  signed-in reader whose API blipped saw "Sign in", and pressing it started
   *  a flow that could not succeed either. */
  unreachable?: boolean;
};

export function AccountMenu() {
  const t = useTranslations("nav");
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<Session | null>(null);
  const [failedSignOut, setFailedSignOut] = useState(false);

  useEffect(() => {
    let live = true;
    fetch(SESSION_ENDPOINT, { cache: "no-store" })
      .then((response) => (response.ok ? (response.json() as Promise<Session>) : null))
      .then((value) => {
        if (live) setSession(value);
      })
      .catch(() => {
        // No API, or no route handler because this is the static build. Either
        // way there is nothing to sign in to, and saying so is not the header's
        // job.
        if (live) setSession(null);
      });
    return () => {
      live = false;
    };
  }, []);

  // Nothing, until it knows — and "the API did not answer" is not knowing. The
  // two cases were collapsed, so an unreachable API rendered "Sign in" at a
  // steward who was already signed in, which is the exact swap the docstring
  // above rejects, plus a link to a flow that would fail the same way.
  if (session === null || session.unreachable) return null;

  if (!session.authenticated) {
    return (
      <Link
        href={{ pathname: "/signin", query: pathname === "/" ? undefined : { next: pathname } }}
        className="text-[color:var(--muted)] transition-colors hover:text-[color:var(--foreground)]"
      >
        {t("signIn")}
      </Link>
    );
  }

  return (
    <span className="flex items-center gap-4">
      {session.is_steward ? (
        <Link
          href="/admin/review"
          className="text-[color:var(--muted)] transition-colors hover:text-[color:var(--foreground)]"
        >
          {t("review")}
        </Link>
      ) : null}
      <Link href="/account" className="max-w-[14rem] truncate font-medium">
        {session.email ?? t("account")}
      </Link>
      <button
        type="button"
        onClick={async () => {
          // The result decides. It was discarded, so a refused logout — the API
          // down, the store unreachable — still cleared the header and called
          // it done. Signing out is the one action where saying it happened
          // when it did not is the whole harm: the session id keeps working for
          // anyone holding it, which is what the person clicking was trying to
          // stop. `/v1/auth/logout`'s own docstring makes that the reason it
          // revokes server-side rather than only clearing the cookie.
          const response = await fetch(SESSION_ENDPOINT, { method: "POST" }).catch(() => null);
          if (!response?.ok) {
            setFailedSignOut(true);
            return;
          }
          setFailedSignOut(false);
          setSession({ authenticated: false });
          router.refresh();
        }}
        className="text-[color:var(--muted)] transition-colors hover:text-[color:var(--foreground)]"
      >
        {t("signOut")}
      </button>
      {failedSignOut ? (
        <span
          role="status"
          className="text-[color:var(--status-alert)]"
        >
          {t("signOutFailed")}
        </span>
      ) : null}
    </span>
  );
}
