import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const headers = {"Cache-Control":"no-store"};
  const remote = process.env.RAILWAY_ENVIRONMENT_ID || process.env.REQUIRE_WORKSPACE_PASSWORD === "1";
  if (remote && !process.env.WORKSPACE_PASSWORD) {
    return NextResponse.json({ok:false}, {status:503, headers});
  }
  try {
    const upstream = process.env.API_INTERNAL_URL || "http://api:8000";
    const response = await fetch(`${upstream.replace(/\/$/, "")}/health`, {
      cache:"no-store", signal:AbortSignal.timeout(4000),
    });
    const status = response.ok ? await response.json() : null;
    const ok = status?.ok === true && status?.store === true;
    return NextResponse.json({ok}, {status:ok ? 200 : 503, headers});
  } catch {
    return NextResponse.json({ok:false}, {status:503, headers});
  }
}
