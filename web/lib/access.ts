import { timingSafeEqual } from "node:crypto";

export function publicInvitation(path: string[], method: string): boolean {
  if (path[0] !== "invitations" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(path[1] || "")) return false;
  return method === "GET" && (path.length === 2 || path.length === 3 && path[2] === "file" || path.length === 4 && path[2] === "preview" && /^\d+$/.test(path[3])) || method === "POST" && path.length === 3 && path[2] === "seal";
}

export function authorized(header: string | null, password: string): boolean {
  if (!header?.startsWith("Basic ")) return false;
  try {
    const decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
    const colon = decoded.indexOf(":");
    if (colon < 0) return false;
    const actual = Buffer.from(decoded.slice(colon + 1));
    const expected = Buffer.from(password);
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  } catch { return false; }
}
