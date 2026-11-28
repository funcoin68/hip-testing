import { NextRequest, NextResponse } from "next/server";

const REALM = "whale-tracker";

function unauthorized(): NextResponse {
  return new NextResponse("Unauthorized", {
    status: 401,
    headers: { "WWW-Authenticate": `Basic realm="${REALM}"` },
  });
}

// Length-normalized, constant-time-ish comparison. The Edge runtime has no
// crypto.timingSafeEqual; acceptable for a single-user gate.
function safeEqual(a: string, b: string): boolean {
  const len = Math.max(a.length, b.length);
  let diff = a.length === b.length ? 0 : 1;
  for (let i = 0; i < len; i++) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

export function middleware(req: NextRequest): NextResponse {
  const user = process.env.BASIC_AUTH_USER;
  const pass = process.env.BASIC_AUTH_PASSWORD;

  // Fail closed: if credentials are not configured, never serve the page.
  if (!user || !pass) {
    return unauthorized();
  }

  const header = req.headers.get("authorization");
  if (!header || !header.startsWith("Basic ")) {
    return unauthorized();
  }

  let decoded: string;
  try {
    decoded = atob(header.slice("Basic ".length));
  } catch {
    return unauthorized();
  }
  const sep = decoded.indexOf(":");
  if (sep === -1) {
    return unauthorized();
  }

  const givenUser = decoded.slice(0, sep);
  const givenPass = decoded.slice(sep + 1);
  if (safeEqual(givenUser, user) && safeEqual(givenPass, pass)) {
    return NextResponse.next();
  }
  return unauthorized();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
