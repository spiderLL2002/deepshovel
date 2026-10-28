#!/usr/bin/env node

import { applyAtlasWorkbenchTheme } from "./atlas-workbench-theme.mjs";
import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROWS_PER_CHUNK = 20_000;
const MAX_STATIC_ASSET_BYTES = 24 * 1024 * 1024;
const OUTPUT_DIRECTORY = path.resolve("public/geochemical-atlas");
const PAYLOAD_FILES = [
  "world-atlas.anomalies.json",
  "world-atlas.basemap.json",
  "world-atlas.boundaries.json",
  "world-atlas.admin1-boundaries.json",
  "world-atlas.context.json",
];
const MAP_FIELDS = [
  "longitude",
  "latitude",
  "record_id",
  "element",
  "medium",
  "spatial_domain",
  "sample_type",
  "measurement_basis",
  "geologic_unit",
  "analytical_method",
  "method_family",
  "method_scope",
  "source_id",
  "original_value_raw",
  "original_unit",
  "censored",
  "value",
  "unit",
  "candidate_anomaly",
  "evidence_layer",
  "confidence_source_evidence_band",
  "confidence_analytical_readiness_band",
  "confidence_spatial_usability_band",
  "confidence_workflow_usability_band",
  "record_sort_order",
  "physical_sample_index",
  "has_geological_background",
];
const MAP_STRING_FIELDS = new Set([
  "record_id",
  "element",
  "medium",
  "spatial_domain",
  "sample_type",
  "measurement_basis",
  "geologic_unit",
  "analytical_method",
  "method_family",
  "method_scope",
  "source_id",
  "original_value_raw",
  "original_unit",
  "unit",
  "evidence_layer",
  "confidence_source_evidence_band",
  "confidence_analytical_readiness_band",
  "confidence_spatial_usability_band",
  "confidence_workflow_usability_band",
]);
const SAMPLE_BOOLEAN_FIELDS = ["censored", "candidate_anomaly"];
const SAMPLE_STRING_ARRAY_FIELDS = [
  "qc_flags",
  "scientific_readiness_reasons",
  "missing_provenance_fields",
  "blocking_qc_flags",
];
const SAMPLE_NUMERIC_FIELDS = [
  "longitude",
  "latitude",
  "original_value",
  "censoring_limit",
  "value",
  "confidence_overall",
  "confidence_source_evidence_score",
  "confidence_analytical_readiness_score",
  "confidence_spatial_usability_score",
  "confidence_workflow_usability_score",
  "confidence_source",
  "confidence_completeness",
  "confidence_method",
  "confidence_spatial",
  "confidence_qc",
  "coordinate_uncertainty_m",
  "coordinate_representation_resolution_m",
];

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function replaceOnce(value, before, after, label) {
  const first = value.indexOf(before);
  if (first < 0 || value.indexOf(before, first + before.length) >= 0) {
    throw new Error(`${label}: expected exactly one source marker`);
  }
  return `${value.slice(0, first)}${after}${value.slice(first + before.length)}`;
}

async function assertAssetSize(filePath) {
  const size = (await stat(filePath)).size;
  if (size > MAX_STATIC_ASSET_BYTES) {
    throw new Error(`${path.basename(filePath)} exceeds the ${MAX_STATIC_ASSET_BYTES}-byte static asset budget`);
  }
  return size;
}

function fieldIndex(fields) {
  return Object.fromEntries(fields.map((name, index) => [name, index]));
}

function pooled(strings, index) {
  return index === -1 || index === null || index === undefined ? null : strings[index];
}

function createMapPayload(samples) {
  const sourceField = fieldIndex(samples.fields);
  const recordIds = samples.rows.map((row) => pooled(samples.strings, row[sourceField.record_id]));
  const recordSortOrder = new Map([...recordIds].sort().map((recordId, index) => [recordId, index]));
  const physicalSamples = new Map();
  const mapStrings = [];
  const mapStringIndex = new Map();
  const intern = (value) => {
    if (value === null || value === undefined) return -1;
    let index = mapStringIndex.get(value);
    if (index === undefined) {
      index = mapStrings.length;
      mapStrings.push(value);
      mapStringIndex.set(value, index);
    }
    return index;
  };

  const rows = samples.rows.map((row, rowIndex) => {
    const recordId = recordIds[rowIndex];
    const sourceId = pooled(samples.strings, row[sourceField.source_id]);
    const sampleId = pooled(samples.strings, row[sourceField.sample_id]);
    const medium = pooled(samples.strings, row[sourceField.medium]);
    const physicalKey = sampleId
      ? `${sourceId}|${sampleId}|${medium}|${row[sourceField.longitude].toFixed(5)}|${row[sourceField.latitude].toFixed(5)}`
      : `record|${recordId}`;
    if (!physicalSamples.has(physicalKey)) physicalSamples.set(physicalKey, physicalSamples.size);
    const candidate = Boolean(row[sourceField.candidate_anomaly]);
    const normalizedValue = row[sourceField.value];

    return MAP_FIELDS.map((name) => {
      if (name === "record_sort_order") return recordSortOrder.get(recordId);
      if (name === "physical_sample_index") return physicalSamples.get(physicalKey);
      if (name === "has_geological_background") {
        return Number(
          row[sourceField.formal_geologic_unit] !== -1
          || row[sourceField.publisher_geological_background] !== -1,
        );
      }
      let value = row[sourceField[name]];
      if (name === "record_id" && !candidate) value = -1;
      if ((name === "original_value_raw" || name === "original_unit") && normalizedValue !== null) value = -1;
      return MAP_STRING_FIELDS.has(name) ? intern(pooled(samples.strings, value)) : value;
    });
  });

  return {
    meta: {
      ...samples,
      fields: MAP_FIELDS,
      strings: mapStrings,
      rows: [],
      web_profile: "map-first-v1",
    },
    physicalSampleCount: physicalSamples.size,
    rows,
  };
}

async function writeChunks(prefix, rows) {
  const chunks = [];
  for (let start = 0, index = 0; start < rows.length; start += ROWS_PER_CHUNK, index += 1) {
    const chunkRows = rows.slice(start, start + ROWS_PER_CHUNK);
    const name = `${prefix}-${String(index).padStart(2, "0")}.json`;
    const filePath = path.join(OUTPUT_DIRECTORY, name);
    const encoded = `${JSON.stringify(chunkRows)}\n`;
    await writeFile(filePath, encoded);
    chunks.push({ name, rows: chunkRows.length, sha256: sha256(encoded) });
  }
  return chunks;
}

async function main() {
  const sourceRoot = process.argv[2] ? path.resolve(process.argv[2]) : null;
  if (!sourceRoot) throw new Error("usage: node scripts/import-geochemical-atlas-web.mjs /path/to/global-geochemical-atlas-demo");
  const sourceDirectory = path.join(sourceRoot, "live");
  const sampleSource = path.join(sourceDirectory, "world-atlas.samples.json");
  const sampleBytes = await readFile(sampleSource);
  const samples = JSON.parse(sampleBytes.toString("utf8"));
  validateSamplePayload(samples);
  const mapPayload = createMapPayload(samples);

  await mkdir(OUTPUT_DIRECTORY, { recursive: true });
  const existing = await readdir(OUTPUT_DIRECTORY);
  await Promise.all(existing
    .filter((name) => /^world-atlas\.(?:samples|map)\.rows-\d+\.json$/u.test(name))
    .map((name) => rm(path.join(OUTPUT_DIRECTORY, name), { force: true })));

  const metadataPath = path.join(OUTPUT_DIRECTORY, "world-atlas.samples.meta.json");
  const mapMetadataPath = path.join(OUTPUT_DIRECTORY, "world-atlas.map.meta.json");
  const fullMetadata = `${JSON.stringify({ ...samples, rows: [] })}\n`;
  const mapMetadata = `${JSON.stringify(mapPayload.meta)}\n`;
  await Promise.all([
    writeFile(metadataPath, fullMetadata),
    writeFile(mapMetadataPath, mapMetadata),
  ]);
  const [chunks, mapChunks] = await Promise.all([
    writeChunks("world-atlas.samples.rows", samples.rows),
    writeChunks("world-atlas.map.rows", mapPayload.rows),
  ]);
  const manifest = {
    schema_version: "deepshovel-geochemical-atlas-web/2.0",
    source_revision: samples.source_revision ?? null,
    source_payload_sha256: sha256(sampleBytes),
    row_count: samples.rows.length,
    physical_sample_count: mapPayload.physicalSampleCount,
    map: {
      meta: path.basename(mapMetadataPath),
      meta_sha256: sha256(mapMetadata),
      fields: MAP_FIELDS,
      chunks: mapChunks,
    },
    full: {
      meta: path.basename(metadataPath),
      meta_sha256: sha256(fullMetadata),
      fields: samples.fields,
      chunks,
    },
    // Kept for consumers of the first Web manifest revision.
    chunks,
  };
  await writeFile(
    path.join(OUTPUT_DIRECTORY, "world-atlas.samples.manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
  );

  for (const name of PAYLOAD_FILES) {
    await copyFile(path.join(sourceDirectory, name), path.join(OUTPUT_DIRECTORY, name));
  }

  const webSource = await readFile(path.join(sourceDirectory, "world-atlas.web.html"), "utf8");
  const before = `const atlasPayloads=[
  ["samples-data","world-atlas.samples.json"],
  ["anomalies-data","world-atlas.anomalies.json"],
  ["basemap-data","world-atlas.basemap.json"],
  ["boundaries-data","world-atlas.boundaries.json"],
  ["admin1-boundaries-data","world-atlas.admin1-boundaries.json"],
  ["context-data","world-atlas.context.json"]
];
const responses=await Promise.all(atlasPayloads.map(async([name,url])=>{const response=await fetch(url,{cache:"force-cache"});if(!response.ok)throw new Error(\`${"${name}"} 加载失败：HTTP ${"${response.status}"}\`);return response}));
atlasBootTitle.textContent="正在解析完整科学数据…";atlasBootDetail.textContent="数据已下载，正在恢复全部点位、来源链、筛选字段与异常证据。";
const [PACK,AF,BASE,BOUNDARIES,ADMIN1,CTX]=await Promise.all(responses.map(response=>response.json()));`;
  const after = `const fetchAtlasPayload=async(name,url,cache="no-cache")=>{const response=await fetch(url,{cache});if(!response.ok)throw new Error(\`${"${name}"} 加载失败：HTTP ${"${response.status}"}\`);return response.json()};
const atlasRequestedTab=new URLSearchParams(location.search).get("tab"),atlasFullSampleViews=new Set(["database","source","confidence","quality","anomaly","combination"]),atlasInitialNeedsFullSamples=atlasFullSampleViews.has(atlasRequestedTab);
const manifest=await fetchAtlasPayload("samples-manifest","world-atlas.samples.manifest.json","no-cache"),initialSampleProfile=atlasInitialNeedsFullSamples?manifest.full:manifest.map;
document.body.dataset.gcaSampleProfile=atlasInitialNeedsFullSamples?"full":"map";
let [sampleMeta,sampleChunks,AF,BASE,BOUNDARIES,ADMIN1,CTX]=await Promise.all([
  fetchAtlasPayload("samples-meta",initialSampleProfile.meta),
  Promise.all(initialSampleProfile.chunks.map(chunk=>fetchAtlasPayload(chunk.name,chunk.name))),
  fetchAtlasPayload("anomalies-data","world-atlas.anomalies.json"),
  fetchAtlasPayload("basemap-data","world-atlas.basemap.json"),
  fetchAtlasPayload("boundaries-data","world-atlas.boundaries.json"),
  fetchAtlasPayload("admin1-boundaries-data","world-atlas.admin1-boundaries.json"),
  fetchAtlasPayload("context-data","world-atlas.context.json")
]);
let PACK={...sampleMeta,rows:sampleChunks.flat()},fullSamplesReady=atlasInitialNeedsFullSamples,fullSamplesPromise=null;
if(PACK.rows.length!==manifest.row_count)throw new Error("samples-data 分片记录数不完整");`;
  let webOutput = replaceOnce(webSource, before, after, "install map-first Atlas sample profile");
  webOutput = webOutput.replace(/<div id="atlasBootStatus"[^>]*>[\s\S]*?<div class="atlas-boot-bar"[^>]*><i><\/i><\/div><\/div><\/div>/u, '<div id="atlasBootStatus" aria-hidden="true"></div>');
  const stringFields = samples.fields.filter((name) => (
    !SAMPLE_NUMERIC_FIELDS.includes(name)
    && !SAMPLE_BOOLEAN_FIELDS.includes(name)
    && !SAMPLE_STRING_ARRAY_FIELDS.includes(name)
  ));
  const unpackStart = `const FIELD=Object.fromEntries(PACK.fields.map((name,index)=>[name,index]));
const pooled=index=>index===-1||index===null||index===undefined?null:PACK.strings[index];
const samples=PACK.rows.map(row=>({`;
  const unpackReplacement = `const SAMPLE_STRING_FIELDS=new Set(${JSON.stringify(stringFields)}),SAMPLE_BOOLEAN_FIELDS=new Set(${JSON.stringify(SAMPLE_BOOLEAN_FIELDS)}),SAMPLE_STRING_ARRAY_FIELDS=new Set(${JSON.stringify(SAMPLE_STRING_ARRAY_FIELDS)}),SAMPLE_SPECIAL_FIELDS=new Set(["record_sort_order","physical_sample_index","has_geological_background"]);
function createSampleDecoder(pack){const positions=Object.fromEntries(pack.fields.map((name,index)=>[name,index])),pooledValue=index=>index===-1||index===null||index===undefined?null:pack.strings[index],assignments=pack.fields.map((name,index)=>[name,index]);return(packed,index)=>{const sample={_mapIndex:index,_recordSortOrder:positions.record_sort_order===undefined?index:packed[positions.record_sort_order],_physicalSampleIndex:positions.physical_sample_index===undefined?undefined:packed[positions.physical_sample_index],_hasGeologicalBackground:positions.has_geological_background===undefined?undefined:Boolean(packed[positions.has_geological_background])};for(const[name,position]of assignments){if(SAMPLE_SPECIAL_FIELDS.has(name))continue;const raw=packed[position];if(SAMPLE_STRING_FIELDS.has(name))sample[name]=pooledValue(raw);else if(SAMPLE_BOOLEAN_FIELDS.has(name))sample[name]=Boolean(raw);else if(SAMPLE_STRING_ARRAY_FIELDS.has(name))sample[name]=(raw||[]).map(pooledValue);else sample[name]=raw}sample.spatial_domain=sample.spatial_domain||"land";sample.confidence_band=sample.confidence_band||"unknown";sample.confidence_source_evidence_band=sample.confidence_source_evidence_band||"unknown";sample.confidence_analytical_readiness_band=sample.confidence_analytical_readiness_band||"unknown";sample.confidence_spatial_usability_band=sample.confidence_spatial_usability_band||"unknown";sample.confidence_workflow_usability_band=sample.confidence_workflow_usability_band||"unknown";sample.evidence_layer=sample.evidence_layer||"provenance_incomplete";sample.provenance_status=sample.provenance_status||"incomplete";sample.quality_control_status=sample.quality_control_status||"not_evaluated";sample.confidence_components={source:sample.confidence_source,completeness:sample.confidence_completeness,method:sample.confidence_method,spatial:sample.confidence_spatial,qc:sample.confidence_qc};return sample}};
const decodeInitialSample=createSampleDecoder(PACK),samples=PACK.rows.map(decodeInitialSample);
PACK=null;sampleMeta=null;sampleChunks=null;
async function ensureFullSamples(){if(fullSamplesReady)return;if(fullSamplesPromise)return fullSamplesPromise;document.body.dataset.gcaSampleProfile="loading-full";fullSamplesPromise=(async()=>{const[fullMeta,fullChunks]=await Promise.all([fetchAtlasPayload("samples-full-meta",manifest.full.meta),Promise.all(manifest.full.chunks.map(chunk=>fetchAtlasPayload(chunk.name,chunk.name)))]),decodeFullSample=createSampleDecoder(fullMeta);let rowIndex=0;for(const chunk of fullChunks)for(const packed of chunk){const target=samples[rowIndex],recordSortOrder=target._recordSortOrder,physicalSampleIndex=target._physicalSampleIndex,mapIndex=target._mapIndex,decoded=decodeFullSample(packed,rowIndex);Object.assign(target,decoded);target._recordSortOrder=recordSortOrder;target._physicalSampleIndex=physicalSampleIndex;target._mapIndex=mapIndex;target._hasGeologicalBackground=Boolean(target.formal_geologic_unit||target.publisher_geological_background);rowIndex+=1}if(rowIndex!==manifest.row_count)throw new Error("完整 samples-data 分片记录数不完整");fullSamplesReady=true;document.body.dataset.gcaSampleProfile="full";document.body.dataset.gcaFullDataReadyMs=String(Math.round(performance.now()-atlasBootStarted))})().catch(error=>{fullSamplesPromise=null;document.body.dataset.gcaSampleProfile="map";throw error});return fullSamplesPromise}
const anomalyFeatures=AF.features||[];`;
  const unpackEnd = `}));
PACK.rows.forEach((packed,index)=>{samples[index].formal_geologic_unit=pooled(packed[FIELD.formal_geologic_unit]);samples[index].publisher_geological_background=pooled(packed[FIELD.publisher_geological_background]);samples[index].geological_context_status=pooled(packed[FIELD.geological_context_status])});
const anomalyFeatures=AF.features||[];`;
  const unpackStartIndex = webOutput.indexOf(unpackStart);
  const unpackEndIndex = webOutput.indexOf(unpackEnd, unpackStartIndex + unpackStart.length);
  if (unpackStartIndex < 0 || unpackEndIndex < 0 || webOutput.indexOf(unpackStart, unpackStartIndex + 1) >= 0) {
    throw new Error("install sparse sample decoder: expected exactly one source block");
  }
  webOutput = `${webOutput.slice(0, unpackStartIndex)}${unpackReplacement}${webOutput.slice(unpackEndIndex + unpackEnd.length)}`;

  webOutput = replaceOnce(
    webOutput,
    "function sampleIdentity(row){return row.sample_id?`${row.source_id}|${row.sample_id}|${row.medium}|${row.longitude.toFixed(5)}|${row.latitude.toFixed(5)}`:`record|${row.record_id}`}",
    "function sampleIdentity(row){if(row._physicalSampleIndex!==undefined)return `sample|${row._physicalSampleIndex}`;return row.sample_id?`${row.source_id}|${row.sample_id}|${row.medium}|${row.longitude.toFixed(5)}|${row.latitude.toFixed(5)}`:`record|${row.record_id}`}",
    "preserve exact physical sample identities",
  );
  webOutput = replaceOnce(
    webOutput,
    "valueComparableIds=new Set(valueStratum?.rows.map(row=>row.record_id)||[]);",
    "valueComparableIds=new Set(valueStratum?.rows.map(row=>row._mapIndex)||[]);",
    "use compact row identity for comparable values",
  );
  webOutput = replaceOnce(
    webOutput,
    "return valueComparableIds.has(row.record_id)?valueColor(row.value):\"#536a70\";",
    "return valueComparableIds.has(row._mapIndex)?valueColor(row.value):\"#536a70\";",
    "color compact comparable rows",
  );
  webOutput = replaceOnce(
    webOutput,
    "String(row.record_id)<String(existing.record_id)",
    "(row._recordSortOrder??row._mapIndex)<(existing._recordSortOrder??existing._mapIndex)",
    "preserve record-id LOD ordering",
  );
  webOutput = replaceOnce(
    webOutput,
    "item.row.record_id===globeSelectedId",
    "item.row._mapIndex===globeSelectedId",
    "find selected globe row by compact identity",
  );
  webOutput = replaceOnce(
    webOutput,
    "globeSelectedId=best.record_id;showRecord(best)",
    "globeSelectedId=best._mapIndex;showRecord(best)",
    "select globe row by compact identity",
  );
  webOutput = replaceOnce(
    webOutput,
    "const present=currentRows.filter(item=>item.formal_geologic_unit||item.publisher_geological_background).length",
    "const present=currentRows.filter(item=>item._hasGeologicalBackground).length",
    "preserve geology coverage without eager verbose strings",
  );
  webOutput = replaceOnce(
    webOutput,
    '${row.value??row.original_value_raw??"未定量"} ${row.unit||row.original_unit||""} · ${row.record_id}`',
    '${row.value??row.original_value_raw??"未定量"} ${row.unit||row.original_unit||""} · ${row.record_id||"点击加载完整记录"}`',
    "avoid exposing a null record id before detail hydration",
  );

  const recordSearchStart = '$("recordSearch").onkeydown=event=>{if(event.key!=="Enter")return;const query=event.target.value.trim(),row=';
  const recordSearchReplacement = '$("recordSearch").onkeydown=async event=>{if(event.key!=="Enter")return;const query=event.target.value.trim();if(!fullSamplesReady){$("detail").replaceChildren();try{await ensureFullSamples()}catch(error){$("detail").innerHTML=`<p class="detail-empty">完整记录加载失败：${esc(error.message)}</p>`;return}}const row=';
  webOutput = replaceOnce(webOutput, recordSearchStart, recordSearchReplacement, "hydrate full record search on demand");

  const viewInitializer = `const initializedViews=new Set();
function initializeView(viewId){if(initializedViews.has(viewId))return;if(viewId==="mapView"){if($("colorMode").value==="value"&&!$("element").value)ensureRelativeRanks();refreshData()}else if(viewId==="databaseView")renderDatabase();else if(viewId==="sourceView"){renderConfidence();renderSources();renderPlatformSources()}else if(viewId==="anomalyView"){renderAnomalyTable();renderStatisticalRegions();requestAnimationFrame(drawAnomalyDensity)}else if(viewId==="combinationView")requestAnimationFrame(renderCombination);initializedViews.add(viewId);document.body.dataset.gcaInitializedViews=[...initializedViews].join(",")}`;
  const stagedViewInitializer = `const initializedViews=new Set(),initializingViews=new Map(),fullSampleViewIds=new Set(["databaseView","sourceView","anomalyView","combinationView"]);
function initializeView(viewId){if(initializedViews.has(viewId)){if(viewId==="combinationView")renderCombination();return Promise.resolve();}if(initializingViews.has(viewId))return initializingViews.get(viewId);const task=(async()=>{if(fullSampleViewIds.has(viewId)&&!fullSamplesReady)await ensureFullSamples();if(viewId==="mapView"){if($("colorMode").value==="value"&&!$("element").value)ensureRelativeRanks();refreshData()}else if(viewId==="databaseView")renderDatabase();else if(viewId==="sourceView"){renderConfidence();renderSources();renderPlatformSources()}else if(viewId==="anomalyView"){if(!initializedViews.has("mapView"))refreshData();else requestAnimationFrame(drawAnomalyDensity);renderAnomalyTable();renderStatisticalRegions()}else if(viewId==="combinationView"){if(!initializedViews.has("mapView"))refreshData();else requestAnimationFrame(renderCombination)}initializedViews.add(viewId);document.body.dataset.gcaInitializedViews=[...initializedViews].join(",")})().catch(error=>console.error(error)).finally(()=>initializingViews.delete(viewId));initializingViews.set(viewId,task);return task}`;
  webOutput = replaceOnce(webOutput, viewInitializer, stagedViewInitializer, "stage full sample views");

  webOutput = replaceOnce(
    webOutput,
    'const rows=samples.filter(row=>row.element===element&&!row.censored&&row.value>0&&row.unit);',
    'const query=$("databaseSearch").value.trim().toLocaleLowerCase(),rows=samples.filter(row=>row.element===element&&!row.censored&&row.value>0&&row.unit&&(!query||databaseSearchText(row).includes(query)));',
    "apply the database search to medium box plots",
  );

  const localDownloadHelper = "function offerLocalJsonDownload(buttonId,filename,data){\n  const button=$(buttonId),noticeId=buttonId+\"Download\",kind=buttonId===\"exportDatabasePatches\"?\"patch\":\"comparison\";\n  const payload=JSON.stringify(data),encoded=new URLSearchParams({kind,payload}).toString();\n  let notice=$(noticeId);\n  if(!notice){notice=document.createElement(\"div\");notice.id=noticeId;notice.className=\"proposal-note\";button.parentElement.after(notice)}\n  const message=document.createElement(\"p\");message.setAttribute(\"role\",\"status\");\n  notice.replaceChildren(message);\n  if(encoded.length>2*1024*1024){message.textContent=\"导出内容超过 2 MiB，请减少本次修订条目后重试。\";return}\n  const frame=document.createElement(\"iframe\"),form=document.createElement(\"form\"),submit=document.createElement(\"button\");\n  frame.name=noticeId+\"Frame\";frame.hidden=true;frame.title=\"导出下载响应\";\n  frame.onload=()=>{try{const body=frame.contentDocument?.body?.textContent?.trim();if(body){const result=JSON.parse(body);if(result.error)message.textContent=\"导出失败：\"+result.error}}catch{message.textContent=\"未能确认下载响应，请重试。\"}};\n  form.method=\"post\";form.action=\"/api/atlas/export\";form.target=frame.name;form.enctype=\"application/x-www-form-urlencoded\";\n  for(const [name,value] of Object.entries({kind,payload})){const input=document.createElement(\"input\");input.type=\"hidden\";input.name=name;input.value=value;form.append(input)}\n  submit.type=\"submit\";submit.className=\"button\";submit.textContent=\"下载 \"+(kind===\"patch\"?\"geochemistry-research-patch.json\":\"geochemistry-comparison-profile.json\");\n  form.append(submit);notice.append(frame,form);\n  form.onsubmit=()=>{message.textContent=\"已向本站请求下载（不保存或执行修订）。若未开始，请点击下载按钮重试；后续修改请重新导出。\"};\n  form.requestSubmit();\n}";
  webOutput = replaceOnce(webOutput,
    "function exportDatabasePatches(){if(!databasePatches.length){alert(\"当前没有待导出的修订提案。\");return}const bundle={schema_version:\"geochemistry-research-patch-v1\",canonical_file:\"geochemistry.csv\",semantics:\"proposal_only_no_direct_mutation\",operations:databasePatches},blob=new Blob([JSON.stringify(bundle,null,2)+\"\\n\"],{type:\"application/json\"}),url=URL.createObjectURL(blob),link=document.createElement(\"a\");link.href=url;link.download=\"geochemistry-research-patch.json\";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}",
    localDownloadHelper + "\n" + "function exportDatabasePatches(){if(!databasePatches.length){alert(\"当前没有待导出的修订提案。\");return}const bundle={schema_version:\"geochemistry-research-patch-v1\",canonical_file:\"geochemistry.csv\",semantics:\"proposal_only_no_direct_mutation\",operations:databasePatches};offerLocalJsonDownload(\"exportDatabasePatches\",\"geochemistry-research-patch.json\",bundle)}",
    "provide a mounted persistent local revision download link");
  webOutput = replaceOnce(webOutput,
    "function exportComparisonProfile(){const profile=comparisonProfile(),x=profile.comparison.x,y=profile.comparison.y;if(!x||!y||x===y){alert(\"请先选择两个不同元素，再导出可复现配置。\");return}const safe=value=>String(value).replace(/[^A-Za-z0-9_-]+/g,\"-\").replace(/^-+|-+$/g,\"\")||\"selection\",blob=new Blob([JSON.stringify(profile,null,2)+\"\\n\"],{type:\"application/json\"}),url=URL.createObjectURL(blob),link=document.createElement(\"a\");link.href=url;link.download=`comparison-${safe(activeRegionKey)}-${safe(x)}-${safe(y)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}",
    "function exportComparisonProfile(){const profile=comparisonProfile(),x=profile.comparison.x,y=profile.comparison.y;if(!x||!y||x===y){alert(\"请先选择两个不同元素，再导出可复现配置。\");return}const safe=value=>String(value).replace(/[^A-Za-z0-9_-]+/g,\"-\").replace(/^-+|-+$/g,\"\")||\"selection\";offerLocalJsonDownload(\"exportComparisonProfile\",`comparison-${safe(activeRegionKey)}-${safe(x)}-${safe(y)}.json`,profile)}",
    "provide a mounted persistent local comparison download link");

  const draftPanel = `<section id="databaseDrafts" class="database-drafts" hidden aria-labelledby="databaseDraftsTitle"><div class="editor-actions"><h3 id="databaseDraftsTitle">待提交修订草稿</h3><button id="clearDatabaseDrafts" type="button" class="button">清空本页草稿</button></div><p class="proposal-note">仅保存在当前页面，尚未写入正式数据库。可逐项撤销后重新填写。</p><ol id="databaseDraftList"></ol></section>`;
  webOutput = replaceOnce(webOutput, '<p id="databasePreviewStatus" class="database-status"></p>', '<p id="databasePreviewStatus" class="database-status"></p>' + draftPanel, "show pending local revision drafts");
  const draftListRenderer = `function nextDraftRecordId(){let index=1;while(databasePatches.some(patch=>patch.record_id==="DRAFT-"+String(index).padStart(3,"0")))index++;return "DRAFT-"+String(index).padStart(3,"0")}
function updatePatchCount(){
  $("databasePatchCount").textContent=fmt.format(databasePatches.length)+" 项待提交";
  const panel=$("databaseDrafts"),list=$("databaseDraftList");
  panel.hidden=!databasePatches.length;
  list.replaceChildren();
  for(const [index,patch] of databasePatches.entries()){
    const item=document.createElement("li"),heading=document.createElement("strong"),value=document.createElement("p"),reason=document.createElement("p"),remove=document.createElement("button");
    heading.textContent=({create:"新增",update:"修改",logical_exclude:"逻辑排除"}[patch.operation]||patch.operation)+" · "+patch.record_id;
    value.textContent="字段："+(patch.field||"—")+" · 拟议值："+(patch.proposed_value||"—");
    reason.textContent="证据 / 理由："+patch.reason;
    remove.type="button";remove.className="button";remove.textContent="撤销此项";
    remove.setAttribute("aria-label","撤销草稿 "+patch.record_id+" 的 "+(patch.field||patch.operation));
    remove.onclick=()=>{databasePatches.splice(index,1);updatePatchCount()};
    item.append(heading,value,reason,remove);list.append(item);
  }
  $("clearDatabaseDrafts").onclick=()=>{databasePatches.length=0;updatePatchCount()};
}`;
  webOutput = replaceOnce(webOutput,
    'function updatePatchCount(){$("databasePatchCount").textContent=`${fmt.format(databasePatches.length)} 项待提交`}',
    draftListRenderer, "allow pending revision drafts to be inspected and withdrawn");
  webOutput = replaceOnce(webOutput,
    'create?`DRAFT-${String(databasePatches.length+1).padStart(3,"0")}`:row?.record_id||""',
    'create?nextDraftRecordId():row?.record_id||""', "avoid duplicate draft IDs after withdrawal");

  const showRecordHydration = `const showRecordHydrationBase=showRecord;
showRecord=function(row,options={}){if(fullSamplesReady)return showRecordHydrationBase(row,options);$("detail").replaceChildren();ensureFullSamples().then(()=>showRecordHydrationBase(row,options)).catch(error=>{$("detail").innerHTML='<p class="detail-empty">完整记录加载失败：'+esc(error.message)+'</p>'})};
`;
  webOutput = replaceOnce(
    webOutput,
    "renderDeliverableCenter();\nupdateViewNavigation();",
    `${showRecordHydration}renderDeliverableCenter();\nupdateViewNavigation();`,
    "hydrate point details on demand",
  );

  const loadingCss = `
.database-drafts{margin:12px 0;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--card);overflow-wrap:anywhere}
.database-drafts[hidden]{display:none}
.database-drafts .editor-actions{justify-content:space-between;align-items:center}
.database-drafts ol{margin:0;padding-left:22px}
.database-drafts li{padding:10px 0;border-top:1px solid var(--line)}
.database-drafts li p{margin:5px 0;white-space:pre-wrap}

#atlasBootStatus{display:none}
.map-card>.card-head>.controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}
.map-card>.card-head>.controls .control select{width:100%;min-width:0}
.mode-explainer{min-height:35px}
@media(min-width:1181px){#regionSummary{min-height:928px}}
@media(max-width:760px){.map-card>.card-head>.controls{grid-template-columns:1fr}}
`;
  webOutput = replaceOnce(webOutput, "</style>", `${loadingCss}</style>`, "add staged data loading styles");
  webOutput = replaceOnce(
    webOutput,
    'requestAnimationFrame(()=>requestAnimationFrame(()=>{document.body.dataset.gcaMapReadyMs=String(Math.round(performance.now()-atlasBootStarted));document.getElementById("atlasBootStatus")?.remove()}));',
    'requestAnimationFrame(()=>requestAnimationFrame(()=>{document.body.dataset.gcaMapReadyMs=String(Math.round(performance.now()-atlasBootStarted));const boot=document.getElementById("atlasBootStatus");if(boot){boot.classList.add("is-ready");boot.setAttribute("aria-hidden","true");boot.removeAttribute("role")}if(window.parent!==window)window.parent.postMessage({type:"deepshovel:geochemical-atlas-ready",version:1},location.origin)}));',
    "hide boot overlay without layout removal",
  );
  webOutput = replaceOnce(
    webOutput,
    '})().catch(error=>{console.error(error);const title=document.getElementById("atlasBootTitle"),detail=document.getElementById("atlasBootDetail"),bar=document.querySelector(".atlas-boot-bar");if(title)title.textContent="图谱数据加载失败";if(detail)detail.textContent=`${error.message}。请检查网络后刷新页面；离线使用请打开 world-atlas.html。`;if(bar)bar.remove()});',
    '})().catch(error=>{console.error(error);const boot=document.getElementById("atlasBootStatus");if(boot){const card=document.createElement("div"),title=document.createElement("strong"),detail=document.createElement("small");card.className="atlas-boot-card";title.textContent="图谱数据加载失败";detail.textContent=`${error.message}。请检查网络后刷新页面；离线使用请打开 world-atlas.html。`;card.append(title,detail);boot.replaceChildren(card);boot.removeAttribute("aria-hidden");boot.setAttribute("role","alert");boot.style.display="grid"}});',
    "show only a necessary Atlas boot error",
  );
  await writeFile(path.join(OUTPUT_DIRECTORY, "world-atlas.web.html"), await applyAtlasWorkbenchTheme(webOutput));

  const publishedFiles = [
    metadataPath,
    mapMetadataPath,
    path.join(OUTPUT_DIRECTORY, "world-atlas.samples.manifest.json"),
    path.join(OUTPUT_DIRECTORY, "world-atlas.web.html"),
    ...PAYLOAD_FILES.map((name) => path.join(OUTPUT_DIRECTORY, name)),
    ...chunks.map((chunk) => path.join(OUTPUT_DIRECTORY, chunk.name)),
    ...mapChunks.map((chunk) => path.join(OUTPUT_DIRECTORY, chunk.name)),
  ];
  const sizes = Object.fromEntries(await Promise.all(publishedFiles.map(async (filePath) => (
    [path.basename(filePath), await assertAssetSize(filePath)]
  ))));
  process.stdout.write(`${JSON.stringify({
    rowCount: samples.rows.length,
    physicalSampleCount: mapPayload.physicalSampleCount,
    fullChunks: chunks.length,
    mapChunks: mapChunks.length,
    sizes,
  }, null, 2)}\n`);
}

export function validateSamplePayload(samples) {
  if (samples.schema_version !== "d3-compact-payload-v1" || !Array.isArray(samples.fields)
    || !Array.isArray(samples.rows) || !Array.isArray(samples.strings)
    || new Set(samples.fields).size !== samples.fields.length
    || samples.strings.some(value => typeof value !== "string")) throw new Error("Invalid compact Atlas payload");
  const required = [...MAP_FIELDS.filter(name => !["record_sort_order", "physical_sample_index", "has_geological_background"].includes(name)), "sample_id", "formal_geologic_unit", "publisher_geological_background"];
  if (required.some(name => !samples.fields.includes(name))) throw new Error("Missing required Atlas field");
  const positions = fieldIndex(samples.fields);
  const stringFields = samples.fields.filter(name => !SAMPLE_NUMERIC_FIELDS.includes(name) && !SAMPLE_BOOLEAN_FIELDS.includes(name) && !SAMPLE_STRING_ARRAY_FIELDS.includes(name));
  const validString = value => value === -1 || value === null || (Number.isInteger(value) && value >= 0 && value < samples.strings.length);
  const ids = new Set();
  for (const row of samples.rows) {
    if (!Array.isArray(row) || row.length !== samples.fields.length) throw new Error("Invalid Atlas row width");
    const lon = row[positions.longitude], lat = row[positions.latitude];
    if (!Number.isFinite(lon) || !Number.isFinite(lat) || Math.abs(lon) > 180 || Math.abs(lat) > 90) throw new Error("Invalid drawable coordinate");
    if (stringFields.some(name => !validString(row[positions[name]]))) throw new Error("Invalid string pool reference");
    for (const name of SAMPLE_STRING_ARRAY_FIELDS.filter(name => name in positions)) {
      if (!Array.isArray(row[positions[name]]) || !row[positions[name]].every(validString)) throw new Error("Invalid string array reference");
    }
    const id = pooled(samples.strings, row[positions.record_id]);
    if (!id || ids.has(id)) throw new Error("Missing or duplicate record ID");
    ids.add(id);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
