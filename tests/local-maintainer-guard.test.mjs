import assert from "node:assert/strict";
import test from "node:test";

const { assertLocalMaintainer, isLocalMaintainerRequest } = await import("../lib/server/evidence-store.ts");

function maintenanceRequest(url, headers) {
  return new Request(url, { method: "DELETE", headers });
}

test("a direct loopback request on the Mac is a local maintainer request", () => {
  for (const address of ["127.0.0.1", "::1", "::ffff:127.0.0.1"]) {
    assert.equal(isLocalMaintainerRequest(maintenanceRequest(
      "http://127.0.0.1:8100/api/internal/artifacts",
      { "cf-connecting-ip": address },
    )), true, address);
  }
  assert.equal(isLocalMaintainerRequest(maintenanceRequest(
    "http://localhost:8100/api/internal/artifacts",
    { "cf-connecting-ip": "127.0.0.1", host: "localhost:8100" },
  )), true);
});

test("public, tunneled, rebinding and unidentified requests are refused", () => {
  const refused = [
    // Cloudflare edge traffic always carries cf-ray / cdn-loop and the real client IP.
    ["http://127.0.0.1:8100/api/internal/artifacts", { "cf-connecting-ip": "203.0.113.7", "cf-ray": "8f0000000000-SJC" }],
    ["http://127.0.0.1:8100/api/internal/artifacts", { "cf-connecting-ip": "127.0.0.1", "cf-ray": "8f0000000000-SJC" }],
    ["http://127.0.0.1:8100/api/internal/artifacts", { "cf-connecting-ip": "127.0.0.1", "cdn-loop": "cloudflare" }],
    ["http://127.0.0.1:8100/api/internal/artifacts", { "cf-connecting-ip": "203.0.113.7" }],
    // No peer address at all fails closed.
    ["http://127.0.0.1:8100/api/internal/artifacts", {}],
    // DNS rebinding: loopback peer, foreign host name.
    ["http://127.0.0.1:8100/api/internal/artifacts", { "cf-connecting-ip": "127.0.0.1", host: "evil.example" }],
    ["https://deepshovel.iiot-shxt.xyz/api/internal/artifacts", { "cf-connecting-ip": "127.0.0.1" }],
  ];
  for (const [url, headers] of refused) {
    const request = maintenanceRequest(url, headers);
    assert.equal(isLocalMaintainerRequest(request), false, JSON.stringify(headers));
    assert.throws(() => assertLocalMaintainer(request), (error) => error.status === 403 && error.code === "MAINTENANCE_LOCAL_ONLY");
  }
});
