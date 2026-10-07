import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const root=fileURLToPath(new URL('../../',import.meta.url));
const evidencePath=(name:string)=>resolve(root,'workspace/evidence',name);
const sha=(path:string)=>createHash('sha256').update(readFileSync(path)).digest('hex');
const trials=new Map<string,{paths:string[],hashes:string[]}>();

async function snapshot(page:any,taskId:string){
  const response=await page.request.get(`/tasks/${taskId}`);expect(response.ok(),await response.text()).toBe(true);
  const state=await response.json();expect(state.task_id).toBe(taskId);
  const events:any[]=[];let cursor=0;
  for(let n=0;n<100;n++){
    const batch=await (await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;events.push(...batch);cursor=batch.at(-1).sequence;
  }
  const jobs:any[]=[];
  for(const id of new Set(events.filter(e=>e.type==='job.queued').map(e=>e.payload.job_id))){
    const job=await (await page.request.get(`/tasks/${taskId}/jobs/${id}`)).json();
    const logs=await (await page.request.get(`/tasks/${taskId}/jobs/${id}/logs`,{timeout:70000})).json();
    jobs.push({job,logs});
  }
  return {state,events,jobs,plan:await (await page.request.get(`/tasks/${taskId}/plan`)).json(),
    artifacts:await (await page.request.get(`/tasks/${taskId}/artifacts`)).json()};
}

async function validateOutcome(page:any,observed:any,taskId:string,model:string,refs:string[],paths:string[],hashes:string[],errors:string[]){
  expect(observed.state.status).toBe('waiting_user');
  const missing=observed.jobs.find(c=>c.job.status==='failed'&&c.logs.stderr?.includes('ModuleNotFoundError'));
  const incompatible=observed.jobs.find(c=>c.job.kind==='environment.prepare'&&c.job.status==='failed'&&c.logs.stderr?.includes('not a supported wheel'));
  const environments=observed.jobs.filter(c=>c.job.kind==='environment.prepare'&&c.job.status==='succeeded');
  const computations=observed.jobs.filter(c=>c.job.kind==='code.execute'&&c.job.status==='succeeded');
  expect(missing).toBeTruthy();expect(incompatible).toBeTruthy();expect(environments).toHaveLength(1);expect(computations).toHaveLength(2);
  const archive=observed.artifacts.find((a:any)=>a.name==='environment.zip');expect(archive).toBeTruthy();
  expect(computations.every(c=>c.job.environment?.artifact_id===archive.artifact_id&&c.job.environment.sha256===archive.sha256&&c.job.image_id===environments[0].job.image_id)).toBe(true);
  const products=[1,2].map(n=>observed.artifacts.find((a:any)=>a.name===`dependency-result-${n}.json`));
  const report=observed.artifacts.findLast((a:any)=>a.name==='dependency-report.md');expect(report).toBeTruthy();
  const completedReads=new Set(observed.events.filter(e=>e.type==='action.observed'&&e.payload.tool==='artifact.read'&&e.payload.result.kind==='completed').map(e=>e.payload.result.action_id));
  for(const product of [...products,report]){
    expect(product).toBeTruthy();expect(observed.events.some(e=>e.type==='action.started'&&e.payload.tool==='artifact.read'&&e.payload.arguments.artifact_id===product.artifact_id&&completedReads.has(e.payload.action_id))).toBe(true);
  }
  for(const product of products){
    expect(await (await page.request.get(product.preview_url)).json()).toEqual({tree_count:3,total_height_m:37.5,mean_height_m:12.5,
      parsed_date:'2026-10-06',numpy_version:'2.2.6',six_version:'1.17.0',dateutil_version:'2.9.0.post0'});
    expect(computations.some(c=>c.job.artifact_ids.includes(product.artifact_id))).toBe(true);
  }
  const text=await (await page.request.get(report.preview_url)).text();expect(text).toMatch(/[\u4e00-\u9fff]/);
  for(const value of ['ModuleNotFoundError','2.2.6','1.17.0','2.9.0.post0','37.5','12.5',refs[0],archive.artifact_id])expect(text).toContain(value);
  const knownIds=new Set([taskId,...refs,...observed.artifacts.map((a:any)=>a.artifact_id),
    ...observed.jobs.map(c=>c.job.job_id),...observed.events.map(e=>e.event_id),
    ...observed.events.filter(e=>e.type==='action.started').map(e=>e.payload.action_id)]);
  const reportedIds=text.match(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi)||[];
  expect(reportedIds.filter(id=>!knownIds.has(id.toLowerCase())),'Report references must identify actual task evidence.').toEqual([]);
  const actualJobIds=new Set(observed.jobs.map(c=>c.job.job_id));
  const claimedJobIds=Array.from(text.matchAll(/(?:作业(?:\s*(?:ID|标识|编号|[0-9一二三四五六七八九十]+))?|job(?:[_\s-]*id)?)\s*[`*]*\s*[:：=]?\s*[`*]*\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/gi),m=>m[1].toLowerCase());
  expect(claimedJobIds.filter(id=>!actualJobIds.has(id)),'An action receipt ID cannot be labelled as a job ID.').toEqual([]);
  for(const job of [missing,incompatible,environments[0],...computations])expect(text).toContain(job.job.job_id);
  expect(observed.events.some(e=>e.type==='action.started'&&['web.search','web.read','role.delegate','memory.read','environment.inspect'].includes(e.payload.tool))).toBe(false);
  paths.forEach((p,i)=>expect(sha(p)).toBe(hashes[i]));
  await page.getByRole('button',{name:'dependency-report.md',exact:true}).last().click();
  await expect(page.locator('.preview-modal pre')).toContainText('ModuleNotFoundError');
  await page.screenshot({path:evidencePath(`workbench-dependencies-${model}-${taskId}.png`),fullPage:true});
  await page.locator('.preview-modal .modal-heading button').click();await page.reload();
  await expect(page.locator('.task-status')).toHaveText('待确认');expect(errors).toEqual([]);
}

for(const model of ['small','large'])test(`${model} repairs and reuses real offline dependencies in the workbench`,async({page})=>{
  test.skip(process.env.RUN_DEPENDENCIES_UI!=='1'||!!process.env.FORESTRY_RECHECK_DEPENDENCIES_TASK,'Explicit new real-model and isolated pip trial.');
  test.setTimeout(480000);
  const lockPath=process.env.FORESTRY_DEPENDENCY_FIXTURE_LOCK;
  expect(lockPath,'Provide the actual verified developer fixture lock.').toBeTruthy();
  const fixture=JSON.parse(readFileSync(resolve(root,lockPath!),'utf8'));
  const directory=resolve(root,'workspace/dependency-ui-fixtures',crypto.randomUUID());mkdirSync(directory,{recursive:true});
  const source=resolve(directory,'heights.csv');writeFileSync(source,'height_m\n8.25\n12.50\n16.75\n');
  const paths=[source,...fixture.wheels.map((w:any)=>w.path)];const hashes=paths.map(sha);
  fixture.wheels.forEach((w:any)=>expect(sha(w.path)).toBe(w.sha256));
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');expect((await page.request.get('/session')).ok()).toBe(true);
  const refs:string[]=[];
  for(const path of paths){
    const reply=await page.request.post('/resources/register',{headers:{origin:new URL(page.url()).origin},
      data:{request_id:crypto.randomUUID(),path,classification:'private',read_only:true}});
    expect(reply.ok(),await reply.text()).toBe(true);refs.push((await reply.json()).resource_id);
  }
  await page.reload();
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await page.locator('.settings-modal select').selectOption(model);
  await expect.poll(async()=>(await (await page.request.get('/settings')).json()).default_model).toBe(model);
  await page.locator('.settings-modal .modal-heading button').click();
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await page.getByRole('combobox',{name:'工作方式',exact:true}).selectOption('H1');
  await page.getByRole('combobox',{name:'任务工具范围',exact:true}).selectOption('custom');
  for(const name of ['写入产物','检查产物','读回产物','查看动作证据','准备隔离依赖','执行受限代码','观察作业','跟踪任务计划'])await page.getByLabel(name,{exact:true}).check();
  for(const name of ['heights.csv',...fixture.wheels.map((w:any)=>w.filename)])await page.locator('.source-chips button').filter({hasText:name}).last().click();
  const wheelMap=Object.fromEntries(fixture.wheels.map((w:any,i:number)=>[w.filename,refs[i+1]]));
  const windows=wheelMap['numpy-2.2.6-cp312-cp312-win_amd64.whl'];
  const correct=[wheelMap['numpy-2.2.6-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'],
    wheelMap['python_dateutil-2.9.0.post0-py2.py3-none-any.whl'],wheelMap['six-1.17.0-py2.py3-none-any.whl']];
  const code=`import csv, json\nfrom pathlib import Path\nimport numpy as np, six, dateutil\nfrom dateutil.parser import parse\nvalues=np.array([float(r['height_m']) for r in csv.DictReader(Path('/inputs/${refs[0]}').open())])\nresult=dict(tree_count=int(values.size),total_height_m=float(values.sum()),mean_height_m=float(values.mean()),parsed_date=parse('2026-10-06').date().isoformat(),numpy_version=np.__version__,six_version=six.__version__,dateutil_version=dateutil.__version__)\nPath('/output/OUTPUT_NAME').write_text(json.dumps(result),encoding='utf-8')\n`;
  await page.locator('.composer textarea').fill(`真实离线依赖修复任务。CSV资源ID=${refs[0]}；已授权wheel映射=${JSON.stringify(wheelMap)}。严格按以下顺序实际操作，不准心算伪造执行、不准联网或宿主安装、不准委派。1) code_execute(code="import numpy\\nprint(numpy.__version__)\\n",input_refs=[])然后wait_job，记录真实ModuleNotFoundError。2) prepare_environment(wheel_refs=[${windows}])然后wait_job，记录Windows wheel在Linux不兼容的真实错误；仅做这一次不兼容试验。3) prepare_environment(wheel_refs=${JSON.stringify(correct)})然后wait_job，成功后用prepared_environment.artifact_id（真实environment.zip ID）作为environment_artifact_id。4) 在同一环境上用下面代码code_execute(input_refs=[${refs[0]}],seconds=120,environment_artifact_id=实际ID)，OUTPUT_NAME第一次替换为dependency-result-1.json，wait_job；再次复用同一环境，OUTPUT_NAME改成dependency-result-2.json，再实际执行并wait_job，不重复安装。5) 实际read_artifact读回两个JSON；write_file保存中文dependency-report.md，包含CSV来源、两次真实失败、修复、真实作业及环境ID、三个依赖的实际版本、3项高度数值及日期、离线安装/源只读和软件夹具不证明科学精度的局限；再实际read_artifact读回报告。最后final_result引用两个JSON和报告ID，待用户验收。\n代码：\n${code}`);
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/tasks');
  await page.locator('.send-button').click();
  const response=await created;expect(response.status()).toBe(202);const taskId=(await response.json()).task_id;
  test.info().annotations.push({type:'dependency_task_id',description:taskId});trials.set(taskId,{paths,hashes});
  await expect.poll(async()=>(await (await page.request.get(`/tasks/${taskId}`)).json()).status,
    {timeout:380000,intervals:[1000,2000]}).toMatch(/^(waiting_user|paused|failed|cancelled|completed)$/);
  const observed=await snapshot(page,taskId);const evidence=evidencePath(`ui-dependencies-${model}-${taskId}.json`);
  writeFileSync(evidence,JSON.stringify({model,task_id:taskId,...observed,all_passed:false,agent_download:false,
    scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete'},null,2));
  await validateOutcome(page,observed,taskId,model,refs,paths,hashes,errors);
  expect(observed.plan.authority).toBe('model_proposed');expect(observed.plan.acceptance).toBe('not_assessed');
  expect(observed.plan.items.length,'Multi-step trial must retain a native plan.').toBeGreaterThan(0);
  expect(observed.plan.items.every((item:any)=>item.status==='completed'),
    'The self-reported plan must be updated after the independently verified workflow finishes.').toBe(true);
  writeFileSync(evidence,JSON.stringify({model,task_id:taskId,...observed,all_passed:true,source_unchanged:true,agent_download:false,
    plan_progress_consistent:true,
    scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete'},null,2));
});

test('recheck the same actual dependency task without executing another model or job',async({page})=>{
  const taskId=process.env.FORESTRY_RECHECK_DEPENDENCIES_TASK;
  test.skip(process.env.RUN_DEPENDENCIES_UI!=='1'||!taskId,'Explicit existing real task and fixture lock.');
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('/');
  expect((await page.request.get('/session')).ok()).toBe(true);
  const observed=await snapshot(page,taskId!);const fixture=JSON.parse(readFileSync(resolve(root,process.env.FORESTRY_DEPENDENCY_FIXTURE_LOCK!),'utf8'));
  const resources=await (await page.request.get('/resources')).json();
  const refs=observed.state.input_refs as string[];
  const sources=refs.map(id=>resources.find((r:any)=>r.resource_id===id));expect(sources.every(Boolean)).toBe(true);
  const paths=sources.map((r:any)=>r.locator);
  const hashes=sources.map((r:any)=>r.name==='heights.csv'?createHash('sha256').update('height_m\n8.25\n12.50\n16.75\n').digest('hex'):
    fixture.wheels.find((w:any)=>w.filename===r.name)?.sha256);
  expect(hashes.every(Boolean)).toBe(true);
  await page.evaluate(id=>localStorage.setItem('forestry.task',id!),taskId);await page.reload();
  await validateOutcome(page,observed,taskId!,'same-task-recheck',refs,paths,hashes,errors);
  expect((await (await page.request.get(`/tasks/${taskId}`)).json()).model_requests).toBe(observed.state.model_requests);
  writeFileSync(evidencePath(`ui-dependencies-same-task-recheck-${taskId}.json`),JSON.stringify({task_id:taskId,...observed,
    all_passed:true,source_unchanged:true,original_trial_preserved:true,additional_model_requests:0,additional_job_submissions:0,
    reason:'Explicit job label matcher excludes a separately labelled environment_artifact_id.',
    agent_download:false,scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete'},null,2));
});

test.afterEach(async({page},info)=>{
  if(info.status===info.expectedStatus)return;
  const taskId=info.annotations.find(a=>a.type==='dependency_task_id')?.description;if(!taskId)return;
  const source=trials.get(taskId);
  writeFileSync(evidencePath(`ui-dependencies-failure-${taskId}.json`),JSON.stringify({task_id:taskId,...await snapshot(page,taskId),
    source_unchanged:source?.paths.every((p,i)=>sha(p)===source.hashes[i]),all_passed:false,error:info.error?.message,
    scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete'},null,2));
});
