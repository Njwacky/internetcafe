import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../owner-dashboard.html', import.meta.url), 'utf8');
// Top-level named functions are separated by the next declaration. Compile the
// extracted real function bodies, not copies of the aggregation/rendering logic.
function source(name) {
  const match = new RegExp('(?:async )?function ' + name + '\\(').exec(html);
  assert.ok(match, name);
  const start = match.index;
  const next = /\n[ \t]*(?:async )?function \w+\(/.exec(html.slice(start + 1));
  return html.slice(start, next ? start + 1 + next.index : undefined).replace(/<\/script>[\s\S]*$/, '');
}
const escape = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this._text = ''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(value) { throw new Error('Unexpected HTML parsing sink: ' + value); }
  get innerHTML() { return escape(this._text); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
function run(names, context = {}) {
  vm.createContext(context);
  vm.runInContext(names.map(source).join('\n'), context);
  return context;
}
function analyticsState() {
  return {days:7,visits:[],stations:[],keys:[],onlineDaily:[],sales:[]};
}
function analyticsContext(state) {
  return run(['isoDay','daysAgoKey','daysLeft','money','currencyTotals','currencyTotalsHTML','buildAnalytics'], {state,TOOL_TO_GROUP:{},esc:escape});
}
function sale(currency = 'R', cents = 10000) {
  const day = new Date().toISOString().slice(0, 10);
  return {shop_id:'SHOP',currency,total_cents:cents,created_at:day+'T10:00:00+00:00'};
}

test('all inline dashboard scripts parse', () => {
  for (const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(m[1]);
});

test('mixed currencies stay separate in totals, averages and the same shop', () => {
  const state = analyticsState();
  state.sales = [sale('R'),sale('USD'),sale('R',20000)];
  const c = analyticsContext(state);
  const a = c.buildAnalytics();
  assert.equal(a.revenueRange.size, 2);
  assert.equal(a.revenueToday.get('R').cents, 30000);
  assert.equal(a.revenueToday.get('USD').cents, 10000);
  assert.equal(a.revenueToday.get('R').count, 2);
  assert.equal(a.salesByShop.size, 2);
  const amounts = c.currencyTotalsHTML(a.revenueRange);
  assert.match(amounts, /R300/);
  assert.match(amounts, /USD100/);
  assert.match(c.currencyTotalsHTML(a.revenueRange,true), /R150/);
  assert.match(c.currencyTotalsHTML(a.revenueRange,true), /USD100/);
});

test('voids and future sales, visits and duration never enter the current range', () => {
  const state = analyticsState();
  state.sales = [sale(),{...sale(),voided_at:'2026-01-01'},{...sale(),created_at:'9999-01-01T00:00:00Z'}];
  state.visits = [{visit_date:'9999-01-01',visit_count:12}];
  state.onlineDaily = [{visit_date:'9999-01-01',machine_id:'future',online_seconds:999}];
  const a = analyticsContext(state).buildAnalytics();
  assert.equal(a.salesInRange.length, 1);
  assert.equal(a.total, 0);
  assert.equal(a.byOnline.size, 0);
});

test('lifetime online time is not mislabeled as selected-period usage', () => {
  const state = analyticsState();
  const today = new Date().toISOString().slice(0,10);
  state.stations = [{machine_id:'old',online_seconds:864000},{machine_id:'current',online_seconds:9999}];
  state.onlineDaily = [{machine_id:'current',visit_date:today,online_seconds:30}];
  const a = analyticsContext(state).buildAnalytics();
  assert.equal(a.byOnline.has('old'), false);
  assert.equal(a.byOnline.get('current').seconds, 30);
});

test('currency labels are escaped before becoming HTML', () => {
  const state = analyticsState();
  state.sales = [sale('<img src=x onerror=evil()>')];
  const c = analyticsContext(state);
  const rendered = c.currencyTotalsHTML(c.buildAnalytics().revenueRange);
  assert.ok(!rendered.includes('<img'));
  assert.match(rendered, /&lt;img/);
});

test('bounded dashboard pagination rejects truncation but permits exactly-full datasets', async () => {
  const c = run(['fetchPaged']);
  function builder(count) {
    return () => ({range:async (a,b) => ({data:Array.from({length:Math.max(0,Math.min(b+1,count)-a)},(_,i) => a+i),error:null})});
  }
  await assert.rejects(c.fetchPaged(builder(1001),1), /partial total/);
  assert.equal((await c.fetchPaged(builder(1000),1)).length, 1000);
  assert.equal((await c.fetchPaged(builder(1001),2)).length, 1001);
  await assert.rejects(c.fetchPaged(() => ({range:async () => ({error:new Error('offline')})})), /offline/);
});

test('failed sales refresh retains last-good data and displays a partial-refresh status', async () => {
  const state = {...analyticsState(),dataErrors:[],salesReady:true,loading:false,lastSync:'LAST GOOD',sales:[sale()]};
  const notices = [];
  const element = {style:{},classList:{toggle(){}},textContent:''};
  function builder(table) {
    const b = {select(){return this},order(){return this},eq(){return this},gte(){return this},limit(){return this},
      range:async () => table === 'shop_sync_sales' ? {data:null,error:new Error('offline')} : {data:[],error:null},
      then(resolve,reject){return Promise.resolve({data:[],error:null}).then(resolve,reject)}};
    return b;
  }
  const c = run(['isoDay','fetchPaged','refreshAll'], {state,sb:{from:builder},$:() => element,
    loadGithubRelease:async () => {}, renderCurrentView(){},toast:(...args) => notices.push(args)});
  await c.refreshAll(true);
  assert.equal(state.sales.length, 1);
  assert.equal(state.lastSync, 'LAST GOOD');
  assert.ok(state.dataErrors.includes('sales'));
  assert.equal(state.loading, false);
  assert.match(notices[0][0], /incomplete/);
});

function templateContext(result) {
  const table = new Element('tbody');
  const calls = [];
  const b = {select(){return this},order(){return this},then(resolve,reject){return Promise.resolve(result).then(resolve,reject)}};
  const c = run(['templateMessage','loadTemplates'], {
    sb:{from:() => b}, document:{getElementById:() => table,createElement:tag => new Element(tag)},
    toggleTemplateActive:(...args) => calls.push(['toggle',...args]),deleteTemplate:(...args) => calls.push(['delete',...args])});
  return {c,table,calls};
}

test('template names, slugs, versions and IDs are text/data, never executable markup', async () => {
  const attack = '<img src=x onerror="globalThis.auditMarker=1">';
  const id = "x'); globalThis.auditMarker=2; ('";
  const {c,table,calls} = templateContext({data:[{category:attack,name:attack,slug:attack,version:attack,id,is_active:true}],error:null});
  await c.loadTemplates();
  assert.equal(table.children.length, 1);
  const row = table.children[0];
  assert.equal(row.children[1].textContent, attack);
  assert.equal(row.children[1].children.length, 0);
  row.children[5].children[0].listeners.click();
  row.children[5].children[1].listeners.click();
  assert.deepEqual(calls, [['toggle',id,false],['delete',id]]);
  assert.equal(c.auditMarker, undefined);
});

test('template errors are rendered as text without HTML parsing', async () => {
  const attack = '<svg onload=evil()>';
  const {c,table} = templateContext({data:null,error:{message:attack}});
  await c.loadTemplates();
  assert.equal(table.textContent, attack);
  assert.equal(table.children[0].children[0].children.length, 0);
});

test('server-side row caps cannot masquerade as complete dashboard totals', async () => {
  const c = run(['fetchPaged']);
  const calls = [];
  const builder = () => ({range:async (a,b) => {
    calls.push([a,b]);
    return {data:Array.from({length:Math.max(0,Math.min(b+1,a+100,350)-a)},(_,i) => a+i),error:null};
  }});
  const rows = await c.fetchPaged(builder,12);
  assert.equal(rows.length, 350);
  assert.equal(new Set(rows).size, 350);
  await assert.rejects(c.fetchPaged(builder,2), /partial total/);
});
