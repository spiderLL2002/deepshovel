import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {POST} from '../app/api/atlas/export/route.ts';

const patch = {schema_version:'geochemistry-research-patch-v1',canonical_file:'geochemistry.csv',semantics:'proposal_only_no_direct_mutation',operations:[{operation:'create',record_id:'DRAFT-001',field:'source_locator',proposed_value:'DOI:测试&x=y',reason:'公开来源',source_record_snapshot:null}]};
const profile = {schema_version:'d3-visualization-profile-v2',title:'As × Cu',subtitle:'test',story:'comparison',theme:'evidence-dark',spatial_scope:'global',default_region:'global',custom_region:null,filters:{},comparison:{x:'As',y:'Cu',medium:null},display:{map_mode:'combined',color_by:'value',anomaly_grid_degrees:2,show_anomaly_points:true,show_anomaly_regions:true}};
const request = (kind,data,headers={}) => new Request('https://deepshovel.iiot-shxt.xyz/api/atlas/export',{method:'POST',headers:{origin:'https://deepshovel.iiot-shxt.xyz',...headers},body:new URLSearchParams({kind,payload:JSON.stringify(data)})});

test('HTTP Atlas exports return exact validated JSON attachments with fixed filenames',async()=>{
  for(const [kind,data,filename] of [['patch',patch,'geochemistry-research-patch.json'],['comparison',profile,'geochemistry-comparison-profile.json']]){
    const response=await POST(request(kind,data));
    assert.equal(response.status,200);assert.equal(response.headers.get('content-disposition'),`attachment; filename="${filename}"`);
    assert.equal(response.headers.get('cache-control'),'private, no-store');
    assert.deepEqual(await response.json(),data);
  }
});
test('Atlas export rejects other kinds, malformed shapes, cross-origin and oversized bodies',async()=>{
  for(const [kind,data] of [['execute',patch],['patch',{...patch,semantics:'mutate'}],['patch',{...patch,operations:[{...patch.operations[0],operation:'delete'}]}],['comparison',{...profile,comparison:{x:'As',y:'As',medium:null}}],['comparison',{...profile,default_region:'custom',custom_region:{label:'bad',bounds:{w:200,s:0,e:220,n:20}}}]]) assert.equal((await POST(request(kind,data))).status,400);
  assert.equal((await POST(request('patch',patch,{origin:'https://other.example'}))).status,403);
  assert.equal((await POST(request('patch',patch,{'content-length':'2097153'}))).status,413);
  const large=new Request('https://deepshovel.iiot-shxt.xyz/api/atlas/export',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:'x'.repeat(2097153)});
  assert.equal((await POST(large)).status,413);
  const malformed=new Request('https://deepshovel.iiot-shxt.xyz/api/atlas/export',{method:'POST',body:new URLSearchParams({kind:'patch',payload:'{bad'})});
  assert.equal((await POST(malformed)).status,400);
  assert.equal((await POST(request('patch',patch,{'content-type':'application/json'}))).status,415);
});

test('Atlas exports preserve same-origin browser forms through the loopback proxy',async()=>{
  const proxied = new Request('http://127.0.0.1:8100/api/atlas/export',{
    method:'POST',headers:{origin:'https://deepshovel.iiot-shxt.xyz','sec-fetch-site':'same-origin'},
    body:new URLSearchParams({kind:'comparison',payload:JSON.stringify(profile)}),
  });
  assert.equal((await POST(proxied)).status,200);
  assert.equal((await POST(request('comparison',profile,{'sec-fetch-site':'cross-site'}))).status,403);
  const frame = readFileSync(new URL('../app/components/geochemical-atlas.tsx',import.meta.url),'utf8');
  const permissions = frame.match(/sandbox="([^"]+)"/)[1].split(' ');
  assert.ok(permissions.includes('allow-forms'),'outer sandbox must allow POST form submission');
  assert.ok(permissions.includes('allow-downloads'),'outer sandbox must allow attachment downloads');
});

test('actual comparisonProfile output is accepted for global and custom regions',async()=>{
  const html=readFileSync(new URL('../public/geochemical-atlas/world-atlas.web.html',import.meta.url),'utf8');
  const source=html.slice(html.indexOf('function comparisonProfile(){'),html.indexOf('\nfunction exportComparisonProfile'));
  for(const key of ['global','custom']){
    const context={activeRegionKey:key,regionDefinition:()=>({label:'测试区域',bounds:{w:120,s:30,e:122,n:32}}),
      $:id=>({value:{comboX:'Ni',comboY:'Cr',comboMedium:'sediment',mapMode:'combined',colorMode:'medium',anomalyGrid:'5'}[id]}),
      overlays:{anomalyPoints:true,anomalyRegions:false}};
    runInNewContext(source,context);
    const data=context.comparisonProfile(),response=await POST(request('comparison',data));
    assert.equal(response.status,200);
    assert.deepEqual(await response.json(),JSON.parse(JSON.stringify(data)));
  }
});
