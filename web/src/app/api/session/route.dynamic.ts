/**
 * The signed-in caller, and how to stop being them — on *this* origin.
 *
 * The header needs to know who is reading, and the layout must not be the place
 * that finds out: reading a cookie in a layout opts every route in the app into
 * per-request rendering, which would throw away the incremental caching that
 * makes a catalog page fast. So the account control is a client component and
 * this is what it calls.
 *
 * Same-origin on purpose. The session cookie belongs to the API's host and is
 * `HttpOnly`, so the browser cannot read it and a cross-origin `fetch` would
 * need `credentials: "include"` plus a CORS allowance for every deployment
 * topology. Here the Next server forwards the cookie it already received, the
 * credential never reaches JavaScript, and nothing depends on where the API is
 * hosted relative to the site.
 *
 * `.dynamic.ts`, so the static build does not try to emit it — a file host has
 * nobody to sign in as. See `pageExtensions` in `next.config.ts`.
 */
import { NextResponse } from "next/server";
import { ApiError, SESSION_COOKIE, logout, me } from "@/lib/api";

export async function GET() {
  try {
    return NextResponse.json(await me());
  } catch (error) {
    // A caller who cannot be identified is anonymous, not an error. The API is
    // unreachable often enough in development that a header which renders an
    // error banner would be the loudest thing on the page.
    const status = error instanceof ApiError ? error.status : 0;
    if (status === 0 || status >= 500) {
      return NextResponse.json({ authenticated: false, unreachable: true });
    }
    return NextResponse.json({ authenticated: false });
  }
}

export async function POST() {
  try {
    await logout();
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    return NextResponse.json({ error: (error as Error).message }, { status });
  }
  const response = NextResponse.json({ authenticated: false });
  // The API revokes the session server-side, which is the part that matters —
  // a cleared cookie alone leaves an id that still works for anyone who copied
  // it. Its `Set-Cookie` lands on this server, though, not on the browser, so
  // where the two share an origin the browser would keep holding a dead cookie.
  // Clearing it here is right in that case and harmless in the other.
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
