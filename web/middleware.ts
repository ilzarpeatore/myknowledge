import { NextResponse, type NextRequest } from "next/server";

// Cheap pre-check only: the API validates the session on every request.
export function middleware(req: NextRequest) {
  if (!req.cookies.has("kos_session")) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!login|api|_next|favicon.ico).*)"],
};
