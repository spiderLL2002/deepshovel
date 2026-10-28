'use strict';
// One input record; jsonld.js RDF conversion, then RDF term/predicate matching.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const args = process.argv.slice(2);
const opts = {};
for (let i=0; i<args.length; i+=2) {
 if (!['--module','--input','--out'].includes(args[i]) || !args[i+1])
  throw Error('Usage: node consume.cjs --module DEPENDENCY_DIRECTORY [--input JSONLD] [--out OUTPUT_DIRECTORY]');
 opts[args[i]]=args[i+1];
}
if (!opts['--module']) throw Error('--module must explicitly name an isolated directory containing package.json and node_modules/jsonld');
const env = path.resolve(opts['--module']);
const req = createRequire(path.join(env, 'package.json'));
const jsonld = req('jsonld');
const input = path.resolve(opts['--input'] || path.join(__dirname, 'input-record.jsonld'));
const out = path.resolve(opts['--out'] || path.join(__dirname, 'rerun-output'));
const sha = x => crypto.createHash('sha256').update(x).digest('hex');
const DS = 'urn:deepshovel:reuse:v1:', PROV = 'http://www.w3.org/ns/prov#';
const TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type';
const XSD = 'http://www.w3.org/2001/XMLSchema#';
(async () => {
 const bytes = fs.readFileSync(input);
 const doc = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(bytes));
 const dataset = await jsonld.toRDF(doc, {documentLoader: async url => {throw Error('Remote context prohibited: '+url);}});
 assert(Array.isArray(dataset));
 const match = (s,p,o) => dataset.filter(q => (!s || q.subject.termType === 'NamedNode' && q.subject.value === s) && q.predicate.termType === 'NamedNode' && q.predicate.value === p && (!o || q.object.termType === 'NamedNode' && q.object.value === o));
 const one = a => {assert.equal(a.length,1); return a[0];};
 const typed = t => one(match(null,TYPE,DS+t)).subject.value;
 const object = (s,p) => one(match(s,p)).object;
 const iri = (s,p) => {const t=object(s,p); assert.equal(t.termType,'NamedNode');return t.value;};
 const literal = (s,p,type='string') => {const t=object(s,p);assert.equal(t.termType,'Literal');assert.equal(t.datatype.value,XSD+type);assert(t.language === undefined || t.language === '');return t.value;};
 const record = typed('PreparedRecord'), prep = typed('RecordPreparation'), imported = typed('ImportedResult');
 assert.equal(iri(record,PROV+'wasGeneratedBy'),prep);
 assert.equal(iri(record,PROV+'wasDerivedFrom'),imported);
 const obs=iri(record,DS+'selectedObservation'), anchor=iri(record,DS+'selectedAnchor');
 assert.equal(obs,typed('SelectedObservation')); assert.equal(anchor,typed('SelectedAnchor'));
 const used=match(prep,PROV+'used').map(q=>{assert.equal(q.object.termType,'NamedNode');return q.object.value;}).sort();
 assert.deepStrictEqual(used,[imported,obs,anchor].sort());
 assert.equal(iri(anchor,DS+'groundsObservation'),obs);
 const source=iri(anchor,DS+'sourceVersion'); assert.equal(source,typed('SourceVersion'));
 const sourceHash=literal(source,DS+'sha256');
 const recovered=literal(record,DS+'originalExport');
 // Compare decoded source literal to recovered RDF literal as exact UTF-8, no reserialization.
 const originalLiteral=one(doc['@graph'].filter(n=>n['@id']===record))['ds:originalExport']['@value'];
 assert(Buffer.from(recovered,'utf8').equals(Buffer.from(originalLiteral,'utf8')));
 const exported=JSON.parse(recovered), payload=exported.payload;
 const canonical=literal(record,DS+'canonicalPayload');
 assert.equal(canonical,exported.canonical_payload);
 const digest=sha(Buffer.from(canonical,'utf8'));
 assert.equal(exported.record_id,'derived-observation:sha256:'+digest);
 assert.equal(literal(record,DS+'recordId'),exported.record_id);
 assert.equal(exported.hash_algorithm,'SHA-256');
 // deepStrictEqual distinguishes booleans, numbers, strings, null, arrays and objects.
 assert.deepStrictEqual(JSON.parse(canonical),payload);
 const prepared={};
 for(const [p,k] of [['preparedValue','value'],['preparedUnit','unit'],['preparedConditions','conditions'],['preparedConclusion','conclusion']]) {
  prepared[k]=literal(record,DS+p);assert.deepStrictEqual(prepared[k],payload.prepared_observation[k]);
 }
 const reading=JSON.parse(literal(record,DS+'sourceReading'));
 assert.deepStrictEqual(reading,payload.source_reading);
 assert.equal(reading.conclusion,prepared.conclusion);
 const identity=JSON.parse(literal(record,DS+'sourceIdentityCheck'));
 assert.deepStrictEqual(identity,payload.source_identity_check);
 assert.equal(identity.observed_source_hash,sourceHash);
 assert(identity.expected_artifact_hashes.includes(sourceHash));
 const recorded=literal(record,DS+'recordedFileCheck'), current=literal(record,DS+'currentFileCheck');
 assert.equal(recorded,identity.check_state);assert.equal(current,'not_rechecked');
 assert.equal(recorded,'verified');
 // Direct domain RDF query: do not decode ds:selectedInspectionPath to obtain rows.
 let route=null;
 if (match(record,DS+'selectedRoute').length) {
  const routeId=iri(record,DS+'selectedRoute');
  assert.equal(literal(record,DS+'routeProjectionStatus'),'unique');
  const sourceNodes=new Map(payload.selected_inspection_path.nodes.map(n=>[n.id,n]));
  const sourceEdges=new Map(payload.selected_inspection_path.edges.map(e=>[e.id,e]));
  const readEdge = edge => {
   const source=iri(edge,DS+'source'),target=iri(edge,DS+'target');
   const row={source_id:literal(source,DS+'recordedNodeId'),source_kind:literal(source,DS+'nodeKind'),relation:literal(edge,DS+'relation'),target_id:literal(target,DS+'recordedNodeId'),target_kind:literal(target,DS+'nodeKind')};
   const original=sourceEdges.get(literal(edge,DS+'recordedEdgeId'));
   assert(original);
   assert.equal(row.source_id,original.source_id);
   assert.equal(row.target_id,original.target_id);
   assert.equal(row.relation,original.relation);
   for(const [resource,id,kind] of [[source,row.source_id,row.source_kind],[target,row.target_id,row.target_kind]]) {
    assert.equal(kind,sourceNodes.get(id).kind);
    assert.deepStrictEqual(JSON.parse(literal(resource,DS+'json')),sourceNodes.get(id));
   }
   assert.deepStrictEqual(JSON.parse(literal(edge,DS+'json')),original);
   return row;
  };
  const steps=match(routeId,DS+'step').map(q=>{
   assert.equal(q.object.termType,'NamedNode');
   const edge=q.object.value, position=Number(literal(edge,DS+'position','integer'));
   assert(Number.isSafeInteger(position) && position>=0);
   return {position,...readEdge(edge)};
  }).sort((a,b)=>a.position-b.position);
  assert(steps.length>0);
  assert.equal(steps[0].source_id,payload.reference.observation_id);
  assert.equal(steps.at(-1).target_id,payload.reference.claim_id);
  steps.forEach((row,i)=>{
   assert.equal(row.position,i);
   if(i) assert.equal(steps[i-1].target_id,row.source_id);
  });
  const grounds=match(record,DS+'selectedGroundsEdge').map(q=>{
   assert.equal(q.object.termType,'NamedNode');
   const row=readEdge(q.object.value);
   assert.equal(row.source_id,payload.reference.anchor_id);
   assert.equal(row.target_id,payload.reference.observation_id);
   assert.equal(row.relation,'grounds');
   return row;
  });
  route={status:'unique',steps,grounds,projected_objects_match_original:true};
 }
 fs.mkdirSync(out,{recursive:false});
 fs.writeFileSync(path.join(out,'recovered-record.json'),Buffer.from(recovered,'utf8'));
 fs.writeFileSync(path.join(out,'dataset.json'),JSON.stringify(dataset,null,2)+'\n');
 const report={library:{package:'jsonld',version:req('jsonld/package.json').version,node:process.version},input_sha256:sha(bytes),rdf_quads:dataset.length,rdf_default_graph_quads:dataset.filter(q=>q.graph.termType==='DefaultGraph').length,rdf_subjects:new Set(dataset.map(q=>q.subject.value)).size,route,lineage:{record,preparation:prep,derived_from:imported,used,selected_anchor:anchor,selected_observation:obs,source_version:source},prepared,source_reading:reading,source_sha256:sourceHash,recorded_file_check:recorded,current_file_check:current,recovered_utf8_bytes:Buffer.byteLength(recovered,'utf8'),recovered_sha256:sha(Buffer.from(recovered,'utf8')),exact_utf8_literal_recovery:true,canonical_payload_sha256:digest,record_id_hash_matches:true,canonical_payload_strict_json_types_match:true,scope:'Two implementations, ONE record when paired with prior Python RDFLib run. This JavaScript run alone is one independent library read; no external application adoption, generic semantic understanding, scientific validation or user benefit inferred. When a selectedRoute projection is present, its ordered steps, node kinds and original relations are read directly from RDF terms and checked against original payload objects; otherwise domain paths remain embedded JSON. No scientific truth is inferred from either representation.'};
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;});
