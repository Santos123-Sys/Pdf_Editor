import { NextRequest, NextResponse } from "next/server";
import { authorized, publicInvitation } from "@/lib/access";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function forward(request: NextRequest, context: { params: Promise<{path: string[]}> }) {
  const { path } = await context.params;
  if (path.some(part => !/^[a-zA-Z0-9_-]+$/.test(part))) return NextResponse.json({detail: "Invalid request path."}, {status: 400});
  const password = process.env.WORKSPACE_PASSWORD;
  const remote = process.env.RAILWAY_ENVIRONMENT_ID || process.env.REQUIRE_WORKSPACE_PASSWORD === "1";
  if (remote && !password) return NextResponse.json({detail: "Set WORKSPACE_PASSWORD before enabling remote access."}, {status: 503});
  // Authorize in the handler as well: middleware is never the security boundary.
  if (password && !publicInvitation(path, request.method) && !authorized(request.headers.get("authorization"), password)) {
    return NextResponse.json({detail: "Workspace password required."}, {status: 401, headers: {"WWW-Authenticate": 'Basic realm="Papertrail workspace"'}});
  }
  if (!["GET", "HEAD"].includes(request.method)) {
    const origin = request.headers.get("origin");
    let crossOrigin = request.headers.get("sec-fetch-site") === "cross-site";
    if (origin) {
      try {crossOrigin ||= new URL(origin).host !== request.headers.get("host");} catch {crossOrigin = true;}
    }
    if (crossOrigin) {
      return NextResponse.json({detail: "Cross-origin changes are not allowed."}, {status: 403});
    }
  }
  const upstream = process.env.API_INTERNAL_URL || "http://api:8000";
  const target = `${upstream}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const length = Number(request.headers.get("content-length") || 0);
  if (length > 51 * 1024 * 1024) return NextResponse.json({detail: "File exceeds the 50 MB limit."}, {status: 413});
  let response: Response;
  try {
    response = await fetch(target, {
      method: request.method,
      headers,
      // Streaming avoids buffering uploaded documents in the Node process.
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      duplex: "half",
      cache: "no-store",
      signal: AbortSignal.timeout(15 * 60 * 1000),
    } as RequestInit & {duplex: string});
  } catch {
    return NextResponse.json({detail: "Document service is starting or unavailable. Refresh the library before retrying a change."}, {status: 503});
  }
  const outputHeaders = new Headers();
  for (const key of ["content-type", "content-disposition", "content-length"]) {
    const value = response.headers.get(key);
    if (value) outputHeaders.set(key, value);
  }
  outputHeaders.set("cache-control", "no-store");
  outputHeaders.set("referrer-policy", "no-referrer");
  outputHeaders.set("x-content-type-options", "nosniff");
  return new NextResponse(response.body, {status: response.status, headers: outputHeaders});
}
export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;
