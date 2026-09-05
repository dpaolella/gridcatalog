import { connection } from "next/server";
import { IS_SNAPSHOT } from "@/lib/api";

/**
 * "Render this on every request" — expressed so that it can be switched off.
 *
 * The obvious way to write it is `export const dynamic = "force-dynamic"`, and
 * that is what these pages used to say. It cannot be conditional: Next reads
 * route segment config by parsing the source, not by evaluating it, so
 * anything but a literal is rejected outright ("Unsupported node type
 * ConditionalExpression"). A static export needs the opposite setting, and one
 * literal cannot be two values.
 *
 * `connection()` moves the decision from parse time to render time. Awaiting it
 * tells Next this render depends on there being a request, which is the same
 * thing `force-dynamic` asserts — and in the static build it is simply not
 * called, because there is no request and the whole point is to write a file.
 *
 * Server components only. `next/server` has no business in a browser bundle.
 */
export async function perRequest(): Promise<void> {
  if (!IS_SNAPSHOT) await connection();
}
