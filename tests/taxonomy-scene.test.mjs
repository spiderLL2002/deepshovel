import assert from "node:assert/strict";
import test from "node:test";

const {
  buildTaxonomyFocusLayout,
  buildTaxonomyScene,
  taxonomyVisualProfile,
  TAXONOMY_POINT_KIND,
  TAXONOMY_SCENE_MAX_CLAIM_POINTS,
} = await import("../app/components/taxonomy-scene.ts");

function hierarchy(claimCount) {
  return [
    { level: "domain", id: "domain", displayName: "Domain", parentId: null, paperCount: 1, claimCount },
    { level: "field", id: "field", displayName: "Field", parentId: "domain", paperCount: 1, claimCount },
    { level: "subfield", id: "subfield", displayName: "Subfield", parentId: "field", paperCount: 1, claimCount },
    { level: "topic", id: "topic", displayName: "Topic", parentId: "subfield", paperCount: 1, claimCount },
  ];
}

function sceneBytes(scene) {
  return [
    scene.overviewCameraBounds,
    scene.positions,
    scene.pointColors,
    scene.pointSizes,
    scene.pointKinds,
    scene.pointOwners,
    scene.nodePointIndices,
    scene.nodeBounds,
    scene.links,
    scene.linkColors,
    scene.linkWidths,
    scene.linkKinds,
  ].reduce((total, array) => total + array.byteLength, 0);
}

test("taxonomy scene is deterministic, finite, and preserves one point per Claim below the GPU budget", () => {
  const first = buildTaxonomyScene(hierarchy(7));
  const second = buildTaxonomyScene(hierarchy(7));

  assert.equal(first.claimPointCount, 7);
  assert.equal(first.claimUnit, 1);
  assert.equal(first.pointKinds.length, 1 + 4 + 7 + 4 * 112);
  assert.equal(first.pointKinds[0], TAXONOMY_POINT_KIND.root);
  assert.ok(first.pointSizes[0] < 0.011);
  assert.deepEqual(Array.from(first.pointColors.slice(0, 4)), [0, 0, 0, 0]);
  assert.deepEqual(first.positions, second.positions);
  assert.deepEqual(
    Array.from(first.nodePointIndices, (pointIndex) => Math.round(Math.hypot(
      first.positions[pointIndex * 2],
      first.positions[pointIndex * 2 + 1],
    ))),
    [28, 85, 163, 296],
  );
  assert.ok(Math.abs(first.overviewCameraBounds[0] + 508.928571) < 0.001);
  assert.ok(Math.abs(first.overviewCameraBounds[2] - 508.928571) < 0.001);
  const firstClaimPoint = 1 + first.nodePointIndices.length;
  assert.ok(Math.hypot(
    first.positions[firstClaimPoint * 2],
    first.positions[firstClaimPoint * 2 + 1],
  ) < 464);
  assert.deepEqual(first.nodeBounds, second.nodeBounds);
  assert.ok(first.positions.every(Number.isFinite));
  assert.ok(first.nodeBounds.every(Number.isFinite));
});

test("focused taxonomy layout opens the selected branch without changing any radius", () => {
  const nodes = [
    { level: "domain", id: "domain-a", displayName: "Domain A", parentId: null, paperCount: 4, claimCount: 4 },
    { level: "domain", id: "domain-b", displayName: "Domain B", parentId: null, paperCount: 1, claimCount: 1 },
    { level: "domain", id: "domain-c", displayName: "Domain C", parentId: null, paperCount: 1, claimCount: 1 },
    ...Array.from({ length: 4 }, (_, index) => ({
      level: "field",
      id: `field-${index}`,
      displayName: `Field ${index}`,
      parentId: "domain-a",
      paperCount: 1,
      claimCount: 1,
    })),
  ];
  const scene = buildTaxonomyScene(nodes);
  const layout = buildTaxonomyFocusLayout(scene, nodes, "domain-a");
  const focusIndex = nodes.findIndex((node) => node.id === "domain-a");
  const focusPoint = scene.nodePointIndices[focusIndex];
  const focusAngle = Math.atan2(layout.positions[focusPoint * 2 + 1], layout.positions[focusPoint * 2]);
  const fieldAngles = nodes
    .map((node, index) => node.parentId === "domain-a" ? scene.nodePointIndices[index] : -1)
    .filter((pointIndex) => pointIndex >= 0)
    .map((pointIndex) => Math.atan2(
      Math.sin(Math.atan2(layout.positions[pointIndex * 2 + 1], layout.positions[pointIndex * 2]) - focusAngle),
      Math.cos(Math.atan2(layout.positions[pointIndex * 2 + 1], layout.positions[pointIndex * 2]) - focusAngle),
    ))
    .sort((left, right) => left - right);

  assert.ok(layout.targetSpan >= 144 * (Math.PI / 180));
  assert.ok(fieldAngles.every((angle, index) => !index || Math.abs((angle - fieldAngles[index - 1]) - layout.targetSpan / 3) < 0.001));
  for (let pointIndex = 1; pointIndex < scene.pointKinds.length; pointIndex += 1) {
    const before = Math.hypot(scene.positions[pointIndex * 2], scene.positions[pointIndex * 2 + 1]);
    const after = Math.hypot(layout.positions[pointIndex * 2], layout.positions[pointIndex * 2 + 1]);
    assert.ok(Math.abs(before - after) < 0.001);
  }
  assert.ok(layout.cameraBounds?.every(Number.isFinite));
  assert.equal(buildTaxonomyFocusLayout(scene, nodes, null).positions, scene.positions);
});

test("taxonomy visual scale is clear for a small corpus and converges at 100,000 Claims", () => {
  const small = taxonomyVisualProfile(527);
  const medium = taxonomyVisualProfile(10_000);
  const large = taxonomyVisualProfile(100_000);

  assert.ok(small.claimPointSize > 4.2);
  assert.ok(small.taxonomyPointScale > 1.49);
  assert.ok(small.overviewScale > 0.89 && small.overviewScale < 0.9);
  assert.ok(small.mobileOverviewScale > small.overviewScale);
  assert.ok(small.claimPointSize > medium.claimPointSize);
  assert.ok(medium.claimPointSize > large.claimPointSize);
  assert.ok(large.claimPointSize > 1.55 && large.claimPointSize < 1.8);
  assert.ok(large.taxonomyPointScale > 1 && large.taxonomyPointScale < 1.05);
  assert.ok(large.overviewScale > 0.98 && large.overviewScale < 1);
  assert.ok(Math.abs(taxonomyVisualProfile(TAXONOMY_SCENE_MAX_CLAIM_POINTS).claimPointSize - 1.55) < Number.EPSILON * 2);
});

test("taxonomy scene uses the primary Topic path so one multi-classified Claim produces one particle", () => {
  const nodes = [
    { level: "domain", id: "domain", displayName: "Domain", parentId: null, paperCount: 1, claimCount: 1, primaryClaimCount: 1 },
    { level: "topic", id: "primary", displayName: "Primary", parentId: "domain", paperCount: 1, claimCount: 1, primaryClaimCount: 1 },
    { level: "topic", id: "secondary", displayName: "Secondary", parentId: "domain", paperCount: 1, claimCount: 1, primaryClaimCount: 0 },
  ];
  const scene = buildTaxonomyScene(nodes);
  assert.equal(scene.claimPointCount, 1);
});

test("taxonomy scene builds 100,000 Claim points within a compact typed-array budget", () => {
  const startedAt = performance.now();
  const scene = buildTaxonomyScene(hierarchy(100_000));
  const elapsed = performance.now() - startedAt;

  assert.equal(scene.claimPointCount, 100_000);
  assert.equal(scene.claimUnit, 1);
  assert.ok(sceneBytes(scene) < 6 * 1024 * 1024, `scene used ${sceneBytes(scene)} bytes`);
  assert.ok(elapsed < 5_000, `scene construction took ${elapsed.toFixed(1)}ms`);
  for (let index = 0; index < scene.positions.length; index += 997) {
    assert.ok(Number.isFinite(scene.positions[index]));
  }
});

test("taxonomy scene bounds future growth instead of creating unbounded render particles", () => {
  const scene = buildTaxonomyScene(hierarchy(TAXONOMY_SCENE_MAX_CLAIM_POINTS * 2));
  assert.equal(scene.claimUnit, 2);
  assert.equal(scene.claimPointCount, TAXONOMY_SCENE_MAX_CLAIM_POINTS);
});

test("aggregate mode keeps the global particle cap across many small Topic allocations", () => {
  const nodes = [
    { level: "domain", id: "domain", displayName: "Domain", parentId: null, paperCount: 400, claimCount: 320_000 },
    ...Array.from({ length: 400 }, (_, index) => ({
      level: "topic",
      id: `topic-${index}`,
      displayName: `Topic ${index}`,
      parentId: "domain",
      paperCount: 1,
      claimCount: 800,
    })),
  ];
  const scene = buildTaxonomyScene(nodes);
  assert.equal(scene.claimPointCount, TAXONOMY_SCENE_MAX_CLAIM_POINTS);
});
