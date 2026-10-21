import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function forward(request: NextRequest, context: { params: Promise<{path: string[]}> }) {
  const { path } = await context.params;
  const upstream = process.env.API_INTERNAL_URL || "http://api:8000";
  const target = `${upstream}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  let response: Response;
  try {
    response = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({detail: "Document service is starting or unavailable. Please retry shortly."}, {status: 503});
  }
  const outputHeaders = new Headers();
  for (const key of ["content-type", "content-disposition"]) {
    const value = response.headers.get(key);
    if (value) outputHeaders.set(key, value);
  }
  outputHeaders.set("cache-control", "no-store");
  return new NextResponse(response.body, {status: response.status, headers: outputHeaders});
}
export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;
