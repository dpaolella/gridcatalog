import { NextResponse } from "next/server";
import { ApiError, reportIssue } from "@/lib/api";

/**
 * The browser posts here; this route posts to the API.
 *
 * A hop rather than a direct call, for one reason that matters: the API's URL
 * is server configuration and does not belong in a bundle a browser
 * downloads. It also means the form works with the API on a private network,
 * which is the normal deployment.
 */
export async function POST(request: Request) {
  const body = await request.json();
  try {
    return NextResponse.json(await reportIssue(body));
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    return NextResponse.json({ error: (error as Error).message }, { status });
  }
}
