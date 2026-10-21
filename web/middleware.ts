import { NextRequest, NextResponse } from "next/server";

export function middleware(request: NextRequest) {
  const password = process.env.WORKSPACE_PASSWORD;
  if (!password && (process.env.RAILWAY_ENVIRONMENT_ID || process.env.REQUIRE_WORKSPACE_PASSWORD === "1")) return new NextResponse("Set WORKSPACE_PASSWORD before enabling remote access.", {status: 503});
  if (!password) return NextResponse.next();
  const path = request.nextUrl.pathname;
  if (path.startsWith("/sign/") || /^\/api\/invitations\/[0-9a-f-]+(?:\/(?:seal|file)|\/preview\/\d+)?$/.test(path)) return NextResponse.next();
  const header = request.headers.get("authorization") || "";
  if (header.startsWith("Basic ")) {
    try {
      const decoded = atob(header.slice(6));
      const index = decoded.indexOf(":");
      if (index >= 0 && decoded.slice(index + 1) === password) return NextResponse.next();
    } catch {}
  }
  return new NextResponse("Workspace password required", {status: 401, headers: {"WWW-Authenticate": 'Basic realm="Papertrail workspace"', "Cache-Control": "no-store"}});
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };

