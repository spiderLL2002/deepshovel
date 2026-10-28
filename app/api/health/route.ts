import { WEB_RELEASE } from "../../../lib/release.ts";

export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json(
    { status: "ok", service: "deepshovel", release: WEB_RELEASE },
    {
      headers: {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
      },
    },
  );
}
