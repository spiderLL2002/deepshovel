import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

const directory = new URL('../public/geochemical-atlas/', import.meta.url);
const readJson = async name => JSON.parse(await readFile(new URL(name, directory), 'utf8'));

// Match the map's inclusive polygon/bounding-box rules. Count repeated physical
// coordinates once at build time, retaining every measurement in the totals.
function pointOnSegment(lon, lat, a, b) {
  const cross = (lon-a[0])*(b[1]-a[1])-(lat-a[1])*(b[0]-a[0]);
  return Math.abs(cross)<=1e-9 && lon>=Math.min(a[0],b[0])-1e-9 && lon<=Math.max(a[0],b[0])+1e-9 && lat>=Math.min(a[1],b[1])-1e-9 && lat<=Math.max(a[1],b[1])+1e-9;
}
function pointInRing(lon, lat, ring) {
  let inside=false, previous=ring[ring.length-1];
  for (const current of ring) {
    if (pointOnSegment(lon,lat,previous,current)) return true;
    if ((current[1]>lat)!==(previous[1]>lat)) {
      const crossing=(previous[0]-current[0])*(lat-current[1])/(previous[1]-current[1])+current[0];
      if(lon<crossing) inside=!inside;
    }
    previous=current;
  }
  return inside;
}
function inRegion(lon, lat, region, countries) {
  const {w,e,s,n}=region.bounds;
  if (!(w<=e ? lon>=w&&lon<=e : lon>=w||lon<=e) || lat<s || lat>n) return false;
  if (!region.country_code) return true;
  const geometry=countries.get(region.country_code)?.geometry||{};
  const polygons=geometry.type==='Polygon'?[geometry.coordinates]:(geometry.coordinates||[]);
  return polygons.some(polygon=>polygon?.length && pointInRing(lon,lat,polygon[0]) && !polygon.slice(1).some(hole=>pointInRing(lon,lat,hole)));
}

/** Keep view-only reports out of the map's critical path. Called by both the
 * regular build and atlas importer so regenerated assets retain this behavior. */
export async function optimizeAtlasMapStartup(html) {
  const [context, manifest, boundaries] = await Promise.all([
    readJson('world-atlas.context.json'), readJson('world-atlas.samples.manifest.json'), readJson('world-atlas.boundaries.json'),
  ]);
  const meta=await readJson(manifest.map.meta);
  const longitude=meta.fields.indexOf('longitude'), latitude=meta.fields.indexOf('latitude');
  const coordinates=new Map();
  let count=0;
  for (const chunk of manifest.map.chunks) {
    for (const row of await readJson(chunk.name)) {
      const lon=row[longitude],lat=row[latitude],key=`${lon},${lat}`;
      const entry=coordinates.get(key)||{lon,lat,count:0};
      entry.count++; coordinates.set(key,entry); count++;
    }
  }
  if(count!==manifest.row_count) throw new Error('Map startup: incomplete measurement chunks');
  const countries=new Map((boundaries.countries||[]).map(country=>[country.iso_a3,country]));
  context.map_region_counts=Object.fromEntries(Object.entries(context.region_presets||{}).map(([key,region])=>[
    key,[...coordinates.values()].reduce((total,point)=>total+(inRegion(point.lon,point.lat,region,countries)?point.count:0),0),
  ]));
  delete context.sources_and_confidence;
  delete context.iteration_backlog;
  delete context.spatial_anomaly_report;
  context.anomaly_report={...context.anomaly_report,groups:[]};
  const payload=JSON.stringify(context)+'\n';
  const version=createHash('sha256').update(payload).digest('hex').slice(0,12);
  await writeFile(new URL('world-atlas.map.context.json',directory),payload);
  html=html.replace(/fetchAtlasPayload\("context-data",[^\n]+\)/, `fetchAtlasPayload("context-data",atlasInitialNeedsFullSamples?"world-atlas.context.json":"world-atlas.map.context.json?v=${version}")`);
  if(!html.includes('async function ensureAtlasDetails()')) {
    html=html.replace('async function ensureFullSamples(){', `let atlasDetailsReady=atlasInitialNeedsFullSamples,atlasDetailsPromise=null;
async function ensureAtlasDetails(){if(atlasDetailsReady)return;if(!atlasDetailsPromise)atlasDetailsPromise=fetchAtlasPayload("context-details","world-atlas.context.json").then(context=>{Object.assign(CTX,context);for(const group of CTX.anomaly_report?.groups||[])anomalyGroups.set(group.group_id,group);atlasDetailsReady=true}).catch(error=>{atlasDetailsPromise=null;throw error});return atlasDetailsPromise}
async function ensureFullSamples(){await ensureAtlasDetails();`);
  }
  html=html.replace('const count=samples.reduce((total,row)=>total+(rowInDefinition(row,region)?1:0),0),option=', 'const count=CTX.map_region_counts?.[key]??samples.reduce((total,row)=>total+(rowInDefinition(row,region)?1:0),0),option=');
  // A filter pass should read each control once, not millions of DOM lookups.
  html=html.replace(/function filteredRows\(\)\{[^\n]+/, block=>{
    if(block.includes('const filterValues=')) return block;
    const ids=[...new Set([...block.matchAll(/\$\("([^"]+)"\)\.value/g)].map(match=>match[1]))];
    const prefix=`const filterValues=Object.fromEntries(${JSON.stringify(ids)}.map(id=>[id,$(id).value])),anomalyOnly=$("anomalyOnly").checked,filterRegion=regionDefinition();`;
    return block.replace('return samples.filter',prefix+'return samples.filter')
      .replaceAll(/\$\("([^"]+)"\)\.value/g,(_,id)=>`filterValues["${id}"]`)
      .replace('!$("anomalyOnly").checked','!anomalyOnly')
      .replace('rowInRegion(row)','rowInDefinition(row,filterRegion)');
  });
  // Classify the compact columns once, rather than checking four field sets
  // for every field of every measurement during the initial decode.
  html=html.replace('assignments=pack.fields.map((name,index)=>[name,index])', 'assignments=pack.fields.map((name,index)=>[name,index,SAMPLE_STRING_FIELDS.has(name)?1:SAMPLE_BOOLEAN_FIELDS.has(name)?2:SAMPLE_STRING_ARRAY_FIELDS.has(name)?3:0]).filter(([name])=>!SAMPLE_SPECIAL_FIELDS.has(name))');
  html=html.replace('for(const[name,position]of assignments){if(SAMPLE_SPECIAL_FIELDS.has(name))continue;const raw=packed[position];if(SAMPLE_STRING_FIELDS.has(name))sample[name]=pooledValue(raw);else if(SAMPLE_BOOLEAN_FIELDS.has(name))sample[name]=Boolean(raw);else if(SAMPLE_STRING_ARRAY_FIELDS.has(name))sample[name]=(raw||[]).map(pooledValue);else sample[name]=raw}', 'for(const[name,position,type]of assignments){const raw=packed[position];sample[name]=type===1?pooledValue(raw):type===2?Boolean(raw):type===3?(raw||[]).map(pooledValue):raw}');
  return html;
}
