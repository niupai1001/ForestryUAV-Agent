import {test,expect} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const root=fileURLToPath(new URL('../../',import.meta.url));
const evidence=(name:string)=>resolve(root,'workspace/evidence',name);
const sha=(bytes:Buffer)=>createHash('sha256').update(bytes).digest('hex');
const buildOnly=process.env.SOURCE_BUILD_ONLY_UI==='1';
const mode=buildOnly?'source-build-only':'source-build';
const trials=new Map<string,{taskId:string;fixture:any}>();

async function snapshot(page:any,taskId:string){
  const state=await(await page.request.get(`/tasks/${taskId}`)).json();
  const artifacts=await(await page.request.get(`/tasks/${taskId}/artifacts`)).json();
  const events:any[]=[];let cursor=0;
  for(let n=0;n<100;n++){
    const batch=await(await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;events.push(...batch);cursor=batch.at(-1).sequence;
  }
  const jobs:any[]=[];
  for(const id of new Set(events.filter(e=>e.type==='job.queued').map(e=>e.payload.job_id))){
    const job=await(await page.request.get(`/tasks/${taskId}/jobs/${id}`)).json();
    const logs=await(await page.request.get(`/tasks/${taskId}/jobs/${id}/logs`,{timeout:70000})).json();
    jobs.push({job,logs});
  }
  return {state,artifacts,events,jobs};
}

test.afterEach(async({page},info)=>{
  const trial=trials.get(info.title);if(!trial||info.status==='passed'||info.status==='skipped')return;
  writeFileSync(evidence(`ui-${mode}-failure-${trial.taskId}.json`),JSON.stringify({task_id:trial.taskId,
    ...await snapshot(page,trial.taskId),errors:info.errors,all_passed:false,general_stage_acceptance:'incomplete'},null,2));
  await page.screenshot({path:evidence(`workbench-${mode}-failure-${trial.taskId}.png`),fullPage:true});
});

for(const model of ['small','large'])test(`${model} ${buildOnly?'builds a wheel then installs':'builds authorized source'} and reuses its environment through UI`,async({page})=>{
  test.skip(process.env.RUN_SOURCE_BUILD_UI!=='1','Explicit real model and isolated source build trial.');
  test.setTimeout(480000);
  expect(process.env.FORESTRY_SOURCE_FIXTURE_LOCK).toBeTruthy();
  const fixture=JSON.parse(readFileSync(resolve(root,process.env.FORESTRY_SOURCE_FIXTURE_LOCK!),'utf8'));
  const records=[fixture.source,...fixture.wheels];
  records.forEach(r=>expect(sha(readFileSync(r.path))).toBe(r.sha256));
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');expect((await page.request.get('/session')).ok()).toBe(true);
  await expect.poll(async()=>{
    const tasks=await(await page.request.get('/tasks')).json();
    return tasks.every((t:any)=>!['created','active','waiting_job','cancelling','publishing'].includes(t.status));
  },{timeout:320000,intervals:[1000,2000]}).toBe(true);
  const refs:string[]=[];
  for(const record of records){
    const grant=await page.request.post('/resources/register',{headers:{origin:new URL(page.url()).origin},
      data:{request_id:crypto.randomUUID(),path:record.path,classification:'private',read_only:true}});
    expect(grant.ok(),await grant.text()).toBe(true);refs.push((await grant.json()).resource_id);
  }
  await page.reload();
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await page.locator('.settings-modal select').selectOption(model);
  await expect.poll(async()=>(await(await page.request.get('/settings')).json()).default_model).toBe(model);
  await page.locator('.settings-modal .modal-heading button').click();
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await page.getByRole('combobox',{name:'工作方式',exact:true}).selectOption('H1');
  await page.getByRole('combobox',{name:'任务工具范围',exact:true}).selectOption('custom');
  for(const label of ['准备隔离依赖','执行受限代码','观察作业','写入产物','读回产物'])await page.getByLabel(label,{exact:true}).check();
  for(const record of records)await page.locator('.source-chips button').filter({hasText:record.filename}).last().click();
  const preparation=buildOnly?`先用prepare_environment(source_refs=["${refs[0]}"],wheel_refs=${JSON.stringify(refs.slice(1))},build_only=true)只构建一次；wait_job返回prepared_wheels（不是已安装环境），读回source-build-lock.json及生成wheel的.metadata。再把prepared_wheels.wheel_artifact_ids交prepare_environment(wheel_artifact_ids=这些实际ID)，不传source_refs，不带构建工具wheel；当前夹具没有运行依赖，可直接离线安装该wheel。观察安装成功后读回environment-lock.json核验安装版本，再复用prepared_environment.artifact_id。`:`用prepare_environment(source_refs=["${refs[0]}"],wheel_refs=${JSON.stringify(refs.slice(1))})在原断网容器真实构建和安装一次；wait_job成功后复用prepared_environment.artifact_id。`;
  await page.locator('.composer textarea').fill(`软件源码构建集成任务：授权源码资源${refs[0]}是fixture.tar.gz（SHA256=${fixture.source.sha256}），Python项目名forestry-build-fixture，版本1.0，模块forestry_build_fixture的answer()返回软件夹具值。构建/运行依赖wheel资源=${JSON.stringify(refs.slice(1))}，分别为setuptools==80.9.0、wheel==0.45.1，均已授权且只读。${preparation}用上述安装成功环境的artifact_id作为environment_artifact_id复用环境两次，不重新安装。每次code_execute真正导入该模块并调用answer()，读取importlib.metadata.version('forestry-build-fixture')，保存source-result-1.json和source-result-2.json，字段answer(数值)、version(字符串)，再wait_job并实际read_artifact读回两个结果。然后write_file保存中文source-build-report.md，记录实际结果/版本、源码资源ID及SHA、生成wheel的实际SHA（读回${buildOnly?'source-build-lock.json':'environment-lock.json'}获取）、成功构建${buildOnly?'、独立安装':''}及两次计算的真实job_id、复用environment.zip的实际ID、断网/只读边界与软件夹具不证明科学精度的局限；实际读回报告后final_result引用两个JSON和报告。无网络请求、不宿主安装、不委派、不重复已成功工作。`);
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/tasks');
  await page.locator('.send-button').click();const response=await created;expect(response.status()).toBe(202);
  const taskId=(await response.json()).task_id;trials.set(test.info().title,{taskId,fixture});
  test.info().annotations.push({type:'source_task_id',description:taskId});
  await expect.poll(async()=>(await(await page.request.get(`/tasks/${taskId}`)).json()).status,
    {timeout:380000,intervals:[1000,2000]}).toMatch(/^(waiting_user|paused|failed|cancelled|completed)$/);
  const observed=await snapshot(page,taskId);
  const path=evidence(`ui-${mode}-${model}-${taskId}.json`);
  const record={model,build_only:buildOnly,task_id:taskId,...observed,fixture,all_passed:false,agent_download:false,
    scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete'};
  writeFileSync(path,JSON.stringify(record,null,2));
  expect(observed.state.status).toBe('waiting_user');expect(observed.state.input_refs).toEqual(refs);
  expect(observed.jobs).toHaveLength(buildOnly?4:3);expect(observed.jobs.every(c=>c.job.status==='succeeded'&&c.job.exit_code===0)).toBe(true);
  const preparations=observed.jobs.filter(c=>c.job.kind==='environment.prepare');expect(preparations).toHaveLength(buildOnly?2:1);
  const build=preparations.find(c=>buildOnly?c.job.source_build_only:c.job.source_recipe);expect(build).toBeTruthy();
  const installation=preparations.find(c=>!c.job.source_build_only);expect(installation).toBeTruthy();
  const computations=observed.jobs.filter(c=>c.job.kind==='code.execute');expect(computations).toHaveLength(2);
  expect(build!.job.source_recipe.map((s:any)=>s.resource_id)).toEqual([refs[0]]);
  const archive=observed.artifacts.find((a:any)=>a.name==='environment.zip');expect(archive).toBeTruthy();
  expect(computations.every(c=>c.job.environment.artifact_id===archive.artifact_id&&c.job.environment.sha256===archive.sha256&&c.job.image_id===build!.job.image_id)).toBe(true);
  const lock=observed.artifacts.find((a:any)=>a.name==='environment-lock.json');expect(lock).toBeTruthy();
  const manifest=await(await page.request.get(lock.preview_url)).json();
  const buildLock=buildOnly?observed.artifacts.find((a:any)=>a.name==='source-build-lock.json'):lock;expect(buildLock).toBeTruthy();
  const buildManifest=buildOnly?await(await page.request.get(buildLock.preview_url)).json():manifest;
  expect(buildManifest.sources).toEqual(build!.job.source_recipe);expect(buildManifest.built_wheels).toHaveLength(1);
  const wheel=observed.artifacts.find((a:any)=>a.name===buildManifest.built_wheels[0].filename);
  expect(wheel.sha256).toBe(buildManifest.built_wheels[0].sha256);expect(wheel.producer_action_id).toBe(build!.job.action_id);
  const metadata=buildOnly?observed.artifacts.find((a:any)=>a.name===buildManifest.built_wheels[0].metadata_filename):undefined;
  if(buildOnly){
    expect(buildManifest.runtime_installation).toBe(false);expect(metadata).toBeTruthy();
    expect(metadata.sha256).toBe(buildManifest.built_wheels[0].metadata_sha256);expect(metadata.producer_action_id).toBe(build!.job.action_id);
    expect(installation!.job.source_recipe).toBeUndefined();
    expect(installation!.job.environment_recipe.map((r:any)=>r.resource_id)).toEqual([wheel.artifact_id]);
    expect(archive.producer_action_id).toBe(installation!.job.action_id);
    expect(manifest.installed).toEqual([{name:'forestry-build-fixture',version:'1.0'}]);
    expect(observed.events.some(e=>e.type==='action.observed'&&e.payload.tool==='job.observe'&&e.payload.result.prepared_wheels?.wheel_artifact_ids.includes(wheel.artifact_id)&&!e.payload.result.prepared_environment)).toBe(true);
  }
  const products=['source-result-1.json','source-result-2.json','source-build-report.md'].map(name=>observed.artifacts.find((a:any)=>a.name===name));
  for(const product of [...products,lock,...(buildOnly?[buildLock,metadata]:[])]){
    expect(product).toBeTruthy();
    expect(observed.events.some(e=>e.type==='action.observed'&&e.payload.tool==='artifact.read'&&e.payload.result.kind==='completed'&&e.payload.result.artifact_id===product.artifact_id)).toBe(true);
  }
  for(const product of products.slice(0,2)){
    expect(await(await page.request.get(product.preview_url)).json()).toEqual({answer:42,version:'1.0'});
    expect(computations.some(c=>c.job.action_id===product.producer_action_id&&c.job.artifact_ids.includes(product.artifact_id)),
      'Required job output must be produced by the actual computation, with its immutable provenance.').toBe(true);
  }
  const text=await(await page.request.get(products[2].preview_url)).text();expect(text).toMatch(/[\u4e00-\u9fff]/);
  for(const value of ['42','1.0',refs[0],fixture.source.sha256,wheel.sha256,archive.artifact_id,...observed.jobs.map(c=>c.job.job_id)])expect(text).toContain(value);
  for(const value of ['只读','断网','软件夹具','精度'])expect(text).toContain(value);
  const known=new Set([taskId,...refs,...observed.artifacts.map((a:any)=>a.artifact_id),...observed.jobs.map(c=>c.job.job_id),
    ...observed.events.filter(e=>e.type==='action.started').map(e=>e.payload.action_id)]);
  expect((text.match(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi)||[]).filter(id=>!known.has(id.toLowerCase()))).toEqual([]);
  expect(observed.events.some(e=>e.type==='action.started'&&['environment.acquire','web.search','web.read','role.delegate'].includes(e.payload.tool))).toBe(false);
  records.forEach(r=>expect(sha(readFileSync(r.path))).toBe(r.sha256));expect(errors).toEqual([]);
  await page.getByRole('button',{name:'source-build-report.md',exact:true}).last().click();
  await expect(page.locator('.preview-modal pre')).toContainText('42');
  await page.screenshot({path:evidence(`workbench-${mode}-${model}-${taskId}.png`),fullPage:true});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:evidence(`workbench-${mode}-${model}-${taskId}-mobile.png`),fullPage:true});
  await page.locator('.preview-modal .modal-heading button').click();await page.reload();
  await expect(page.locator('.task-status')).toHaveText('待确认');
  writeFileSync(path,JSON.stringify({...record,all_passed:true,source_unchanged:true},null,2));
});
