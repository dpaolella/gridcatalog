import { NextResponse } from "next/server";
import { ApiError, submitDataset } from "@/lib/api";

export async function POST(request: Request) {
  const body = await request.json();
  try {
    return NextResponse.json(await submitDataset(body));
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    return NextResponse.json({ error: (error as Error).message }, { status });
  }
}
