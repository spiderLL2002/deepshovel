import test from 'node:test';
import assert from 'node:assert/strict';
import {validateSamplePayload} from '../scripts/import-geochemical-atlas-web.mjs';

test('Atlas input validation accepts zero rows and validates the input contract instead of a historical hash', () => {
  // Schema-only fixture: tests also run in a clone without reference data.
  const fields = [
    'longitude', 'latitude', 'record_id', 'element', 'medium', 'spatial_domain',
    'sample_type', 'measurement_basis', 'geologic_unit', 'analytical_method',
    'method_family', 'method_scope', 'source_id', 'original_value_raw',
    'original_unit', 'censored', 'value', 'unit', 'candidate_anomaly',
    'evidence_layer', 'confidence_source_evidence_band',
    'confidence_analytical_readiness_band', 'confidence_spatial_usability_band',
    'confidence_workflow_usability_band', 'sample_id', 'formal_geologic_unit',
    'publisher_geological_background',
  ];
  const payload = {schema_version:'d3-compact-payload-v1', fields, rows:[], strings:[]};
  assert.doesNotThrow(()=>validateSamplePayload(payload));
  assert.throws(()=>validateSamplePayload({...payload,rows:[[0]]}),/row width/);
  assert.throws(()=>validateSamplePayload({...payload,fields:['record_id']}),/required/);
});

test('revisiting combination analysis recomputes the current region after map changes', async () => {
  const {readFileSync} = await import('node:fs');
  const {runInNewContext} = await import('node:vm');
  const html = readFileSync(new URL('../public/geochemical-atlas/world-atlas.web.html', import.meta.url), 'utf8');
  const initializer = html.match(/^function initializeView\(viewId\).*$/m)?.[0];
  assert.ok(initializer);
  const importer = readFileSync(new URL('../scripts/import-geochemical-atlas-web.mjs', import.meta.url), 'utf8');
  assert.ok(importer.includes(initializer), 're-importing the atlas must preserve the refresh fix');
  let region = 'China';
  let displayedRegion;
  let renders = 0;
  const context = {
    initializedViews: new Set(['mapView']), initializingViews: new Map(),
    fullSampleViewIds: new Set(['combinationView']), fullSamplesReady: true,
    document: {body: {dataset: {}}}, console,
    requestAnimationFrame: (callback) => callback(),
    renderCombination: () => { displayedRegion = region; renders++; },
  };
  runInNewContext(initializer, context);
  await context.initializeView('combinationView');
  assert.equal(displayedRegion, 'China');
  // Changing scope on the map leaves analysis hidden; returning must refresh it.
  region = 'custom';
  region = 'global';
  await context.initializeView('combinationView');
  assert.equal(displayedRegion, 'global');
  assert.equal(renders, 2);
  await context.initializeView('mapView');
  assert.equal(renders, 2, 'map visits should not eagerly recompute a hidden analysis');
});

test('database box plots clear their prior layout when the search has no matching records', async () => {
  const {readFileSync} = await import('node:fs');
  const {runInNewContext} = await import('node:vm');
  const html = readFileSync(new URL('../public/geochemical-atlas/world-atlas.web.html', import.meta.url), 'utf8');
  const render = html.match(/^function renderMediumBoxPlot\(element\).*$/m)?.[0];
  assert.ok(render);
  let query = '';
  const messages = [];
  const canvas = new Proxy({fillText: (text) => messages.push(text)}, {get: (target, key) => target[key] ?? (() => {})});
  const context = {
    samples: Array.from({length: 6}, (_, i) => ({element: 'Zn', value: i + 1, unit: 'ppm', medium: 'rock'})),
    $: () => ({value: query}),
    chartCanvas: () => ({context: canvas, width: 500, height: 260}),
    databaseSearchText: (row) => `${row.element} ${row.medium}`.toLocaleLowerCase(),
    mediumStyle: () => ['#555555', '岩石'], databaseBoxLayout: null,
  };
  runInNewContext(render, context);
  context.renderMediumBoxPlot('Zn');
  assert.equal(context.databaseBoxLayout.items[0].n, 6);
  query = 'NONEXISTENT-IAB-QA-20261020';
  context.renderMediumBoxPlot('Zn');
  assert.equal(context.databaseBoxLayout, null);
  assert.match(messages.at(-1), /没有可绘制的定量记录/);
});

test('pending revision drafts can be inspected, individually withdrawn, and cleared without touching source records', async () => {
  const {readFileSync} = await import('node:fs');
  const {runInNewContext} = await import('node:vm');
  const html = readFileSync(new URL('../public/geochemical-atlas/world-atlas.web.html', import.meta.url), 'utf8');
  const renderer = html.match(/function updatePatchCount\(\)\{[\s\S]*?\n\}/)?.[0];
  assert.ok(renderer);
  const importer = readFileSync(new URL('../scripts/import-geochemical-atlas-web.mjs', import.meta.url), 'utf8');
  assert.ok(importer.includes(renderer), 'importer must preserve the pending draft controls');
  const makeNode = (tag) => ({tag, children: [], append(...nodes) {this.children.push(...nodes);}, replaceChildren(...nodes) {this.children = nodes;}, setAttribute() {}});
  const nodes = Object.fromEntries(['databasePatchCount', 'databaseDrafts', 'databaseDraftList', 'clearDatabaseDrafts'].map(id => [id, makeNode('div')]));
  const source = {record_id:'original', value:10};
  const drafts = [
    {operation:'create',record_id:'IAB-QA-disposable',field:'qa_note',proposed_value:'<not html>',reason:'test reason'},
    {operation:'update',record_id:'original',field:'value',proposed_value:'12',reason:'page 2',source_record_snapshot:source},
  ];
  const context = {databasePatches:drafts, $:id=>nodes[id], document:{createElement:makeNode}, fmt:new Intl.NumberFormat('en')};
  runInNewContext(renderer + "\n" + html.match(/^function nextDraftRecordId.*$/m)[0], context);
  context.updatePatchCount();
  assert.equal(nodes.databaseDrafts.hidden, false);
  assert.equal(nodes.databaseDraftList.children.length, 2);
  assert.match(nodes.databaseDraftList.children[0].children[1].textContent, /<not html>/);
  assert.match(nodes.databaseDraftList.children[0].children[2].textContent, /test reason/);
  nodes.databaseDraftList.children[0].children[3].onclick();
  assert.equal(drafts.length, 1);
  assert.equal(drafts[0].record_id, 'original');
  assert.equal(nodes.databasePatchCount.textContent, '1 项待提交');
  drafts[0].record_id = 'DRAFT-002';
  assert.equal(context.nextDraftRecordId(), 'DRAFT-001', 'withdrawal must not generate an ID already held by another draft');
  nodes.clearDatabaseDrafts.onclick();
  assert.equal(drafts.length, 0);
  assert.equal(nodes.databaseDraftList.children.length, 0);
  assert.equal(nodes.databaseDrafts.hidden, true);
  assert.equal(nodes.databasePatchCount.textContent, '0 项待提交');
  assert.deepEqual(source, {record_id:'original',value:10});
});
