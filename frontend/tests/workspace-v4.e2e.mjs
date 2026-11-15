import {chromium,expect} from '@playwright/test';
import {existsSync,mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
const base=process.env.WORKBENCH_URL||'http://127.0.0.1:8014';
const exe=['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Google/Chrome/Application/chrome.exe'].find(existsSync);
const browser=await chromium.launch({headless:true,...(exe?{executablePath:exe}:{channel:'msedge'})});
const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',reducedMotion:'reduce'});
const page=await context.newPage();page.setDefaultTimeout(20000);
const output=resolve('../docs/validation');const shots=resolve('../docs/screenshots');mkdirSync(output,{recursive:true});mkdirSync(shots,{recursive:true});
const checks=[],errors=[],external=[],screenshots=[];
page.on('pageerror',e=>errors.push(String(e)));
await context.route('**/*',route=>{const u=new URL(route.request().url());if(['http:','https:'].includes(u.protocol)&&!['127.0.0.1','localhost'].includes(u.hostname)){external.push(u.href);return route.abort()}return route.continue()});
async function ok(name,assertion){await assertion;checks.push(name)}
async function json(path,body){const r=body===undefined?await context.request.get(base+'/api'+path):await context.request.post(base+'/api'+path,{data:body,timeout:90000});if(!r.ok())throw Error(path+' '+r.status()+' '+(await r.text()).slice(0,1000));return r.json()}
async function shot(name){await page.evaluate(async()=>{await document.fonts.ready;document.activeElement?.blur();window.scrollTo(0,0);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});await page.screenshot({path:resolve(shots,'v4-'+name+'.png'),fullPage:true});screenshots.push('docs/screenshots/v4-'+name+'.png')}
async function overflow(name){await ok(name,expect.poll(()=>page.evaluate(()=>Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)-innerWidth)).toBeLessThanOrEqual(1))}
async function task(instruction){const t=await json('/tasks',{instruction,engine:'demo'});const parsed=await json('/tasks/'+t.task_id+'/parse',{});return {id:t.task_id,parsed}}
try{
 await page.goto(base+'/workbench',{waitUntil:'networkidle'});
 await ok('platform title',expect(page).toHaveTitle('运行方式智能调整平台B'));
 await ok('logos',expect(page.locator('.app-header img')).toHaveCount(3));
 await ok('seven workflow steps',expect(page.locator('.steps button')).toHaveCount(7));
 await ok('unified network map loads',expect(page.getByTestId('grid-scene')).toContainText('905 节点'));
 await ok('missing geolocation is explicit',expect(page.getByTestId('grid-scene')).toContainText('设备坐标待接入'));
 await overflow('home 1440 overflow');await shot('home');
 const request=page.waitForResponse(r=>r.url().endsWith('/api/tasks')&&r.request().method()==='POST');
 await page.getByTestId('instruction-input').fill('第1–8时段，母线43有功负荷提高20%。');await page.getByTestId('parse-instruction').click();const first=await (await request).json();
 await ok('L1 parse',expect.poll(async()=>{const t=await json('/tasks/'+first.task_id);return t.parse?.level}).toBe('L1'));
 await page.getByTestId('step-3').click();await ok('dynamic model entry',expect(page.getByTestId('dynamic-model')).toBeVisible());
 await page.getByTestId('confirm-spec').click();await ok('L1 confirmation',expect(page.locator('.confirmed-actions')).toContainText('已确认'));
 await page.getByTestId('tab-comparison').click();await ok('three-way comparison',expect(page.getByTestId('model-comparison').locator(':scope > section')).toHaveCount(3));await shot('comparison');
 await page.getByTestId('tab-checks').click();const checkResponse=page.waitForResponse(r=>r.url().endsWith('/check'),{timeout:90000});await page.getByTestId('run-check').click();const report=await (await checkResponse).json();
 await ok('five checks executed',expect(report.layers.length).toBe(5));await ok('L1 build success',expect(report.build.status).toBe('pass'));await ok('L1 no blocking error',expect(report.errors).toBe(0));await shot('checks');
 const riskResponse=page.waitForResponse(r=>r.url().endsWith('/evaluate'),{timeout:90000});await page.getByTestId('go-baseline-risk').click();const risk=await (await riskResponse).json();
 await ok('pre-solve risk stage',expect(risk.evaluation_stage).toBe('baseline'));await ok('risk has no invented after state',expect(risk.after).toBeNull());await ok('pre-solve risk does not complete solver',expect(page.getByTestId('step-5')).not.toHaveClass(/complete/));await ok('pre-solve risk does not complete results',expect(page.getByTestId('step-6')).not.toHaveClass(/complete/));await ok('risk negotiation rendered',expect(page.locator('.risk-context-pill')).toContainText('求解前'));
 await shot('risk');await page.locator('.suggestion-card .el-checkbox').first().click();await page.getByTestId('apply-adjustment').click();
 await ok('negotiation returns to M3',expect(page.getByTestId('dynamic-model')).toBeVisible());const negotiated=await json('/tasks/'+first.task_id);await ok('negotiation creates child draft',expect(negotiated.draft.parent_version).toBe(1));await ok('new draft requires input',expect(page.getByTestId('confirm-spec')).toBeDisabled());
 const examples=[['L2','高温天气，线路L155热稳限额下降。'],['L3','第1–16时段，开关次数总上限为4次。'],['L4','高温天气，线路L155热稳限额下降，尽量少动开关。']];
 for(const [level,instruction] of examples){const t=await task(instruction);await ok(level+' automatic classification',expect(t.parsed.level).toBe(level));await page.goto(base+'/workbench?task='+t.id,{waitUntil:'networkidle'});await ok(level+' page loaded',expect(page.locator('.system-message')).toContainText(level));}
 const uc=await task('第1–8时段，母线43有功负荷提高20%。');await page.goto(base+'/workbench?task='+uc.id+'&view=spec',{waitUntil:'networkidle'});
 await page.getByTestId('edit-model').click();await page.getByTestId('generator-mode').click();await page.getByRole('option',{name:'机组启停',exact:true}).click();await page.getByTestId('initial-duration').locator('input').fill('4');await page.getByTestId('initial-duration').locator('input').blur();await shot('model-config');
 await page.getByTestId('apply-model-config').click();await ok('UC model configuration',expect(page.getByTestId('dynamic-model')).toContainText('机组启停'));await ok('real declared integer count',expect(page.getByTestId('binary-count')).toHaveText('14,976'));
 await page.getByTestId('confirm-spec').click();await ok('UC confirmed',expect(page.locator('.confirmed-actions')).toContainText('已确认'));await page.getByTestId('tab-checks').click();const ucResponse=page.waitForResponse(r=>r.url().endsWith('/check'),{timeout:90000});await page.getByTestId('run-check').click();const ucCheck=await (await ucResponse).json();await ok('UC check and build',expect([ucCheck.errors,ucCheck.build.status]).toEqual([0,'pass']));
 await page.getByTestId('go-solver').click();await ok('blackbox visible',expect(page.getByRole('heading',{name:'求解黑盒',exact:false})).toBeVisible());await page.getByTestId('ack-warnings').click();await page.getByTestId('run-solver').click();await ok('mock result is baseline only',expect(page.getByTestId('go-results')).toBeEnabled());await page.getByTestId('go-results').click();await ok('result identity',expect(page.locator('.content-pad')).toContainText('基态展示'));
 await page.getByRole('tab',{name:'地图与网架',exact:true}).click();await ok('result network loads',expect(page.getByTestId('grid-scene')).toContainText('905 节点'));await shot('results-map');
 const bad=await task('第1时段，机组G237有功出力设为2000MW。');const diagnosis=await json('/tasks/'+bad.id+'/check',{});await ok('infeasible output located',expect(diagnosis.diagnosis.conflicts.some(x=>x.code==='E211')).toBe(true));await page.goto(base+'/workbench?task='+bad.id+'&view=checks',{waitUntil:'networkidle'});await ok('conflict UI',expect(page.getByTestId('conflict-diagnosis')).toContainText('G237'));
 const cost=await task('采用固定拓扑AC-OPF，以发电成本最小为目标。');const missing=await context.request.post(base+'/api/tasks/'+cost.id+'/confirm',{data:{}});await ok('cost data cannot be invented',expect(missing.status()).toBe(409));
 await page.goto(base+'/data?tab=measurements',{waitUntil:'networkidle'});await ok('unified package ready',expect(page.locator('.package-banner')).toContainText('已载入'));
 await page.getByTestId('measurement-upload').setInputFiles({name:'static.csv',mimeType:'text/csv',buffer:Buffer.from('point_id,device_id,node_id,measurement,unit\nP001,D001,B43,PG,MW\n')});await ok('measurement preview',expect(page.locator('.el-table')).toContainText('P001'));await shot('data');
 await page.getByRole('tab',{name:'设备地理定位'}).click();await ok('coordinate import available',expect(page.getByTestId('coordinate-upload')).toHaveCount(1));
 await page.goto(base+'/decomposition?task='+uc.id,{waitUntil:'networkidle'});await ok('aggregation rows',expect(page.locator('body')).toContainText('500 kV'));await ok('CMDP status',expect(page.getByTestId('cmdp-panel')).toContainText('待开发'));await shot('aggregation');
 await page.goto(base+'/history',{waitUntil:'networkidle'});await ok('historical result N1 base',expect(page.getByTestId('history-n1-before')).toHaveText('103 / 107'));await ok('historical result N1 after',expect(page.getByTestId('history-n1-after')).toHaveText('102 / 106'));await page.getByTestId('history-tab-map').click();await ok('history map and schedule',expect(page.locator('.switching-matrix')).toContainText('L151'));await shot('history-map');
 await page.getByTestId('history-tab-sources').click();await ok('history clean settings',expect(page.locator('body')).not.toContainText('本次接入做了什么'));await ok('no audit descriptions',expect(page.locator('body')).not.toContainText('SHA-256'));
 await page.goto(base+'/evaluation',{waitUntil:'networkidle'});const ev=page.waitForResponse(r=>r.url().endsWith('/run'),{timeout:90000});await page.getByTestId('run-evaluation').click();const evaluated=await (await ev).json();await ok('built-in evaluation all pass',expect([evaluated.total,evaluated.passed]).toEqual([10,10]));await shot('evaluation');
 await page.goto(base+'/features',{waitUntil:'networkidle'});await ok('all modules listed',expect(page.locator('body')).toContainText('OTS → CMDP'));await ok('planned RL',expect(page.locator('body')).toContainText('安全约束强化学习'));await shot('features');
 for(const width of [1280,800]){
  await page.setViewportSize({width,height:1000});
  for(const route of ['/workbench?task='+uc.id+'&view=spec','/data','/decomposition','/history']){await page.goto(base+route,{waitUntil:'networkidle'});await overflow(width+' '+route+' overflow');}
 }
 await ok('no browser runtime errors',expect(errors).toEqual([]));await ok('offline network',expect(external).toEqual([]));
 const result={status:'passed',assertions:checks.length,checks,page_errors:errors,external_requests:external,screenshots};writeFileSync(resolve(output,'frontend-v4.json'),JSON.stringify(result,null,2),'utf8');console.log(JSON.stringify(result));
}catch(e){await page.screenshot({path:resolve(shots,'v4-failure.png'),fullPage:true}).catch(()=>{});writeFileSync(resolve(output,'frontend-v4.json'),JSON.stringify({status:'failed',checks,error:String(e),page_errors:errors,external_requests:external,screenshots},null,2),'utf8');console.error(e);process.exitCode=1}finally{await browser.close()}
