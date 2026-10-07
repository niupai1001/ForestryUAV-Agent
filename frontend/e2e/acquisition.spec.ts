import {test,expect} from '@playwright/test';
import {writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

const wheel={name:'six',version:'1.17.0',filename:'six-1.17.0-py2.py3-none-any.whl'};
const trials=new Map<string,string>();
test.afterEach(async({page},info)=>{
  const taskId=trials.get(info.title);if(!taskId||info.status==='passed'||info.status==='skipped')return;
  const state=await(await page.request.get(`/tasks/${taskId}`)).json();
  const approvals=await(await page.request.get(`/tasks/${taskId}/approvals`)).json();
  const events:any[]=[];let cursor=0;
  for(let n=0;n<100;n++){
    const batch=await(await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;events.push(...batch);cursor=batch.at(-1).sequence;
  }
  writeFileSync(`../workspace/evidence/ui-acquisition-failure-${taskId}.json`,JSON.stringify({task_id:taskId,state,approvals,events,all_passed:false,
    errors:info.errors,general_stage_acceptance:'incomplete',scientific_accuracy:'not_assessed'},null,2));
});
for(const model of ['small','large'])test(`${model} acquires a public wheel after exact UI approval`,async({page})=>{
  test.skip(process.env.RUN_ACQUISITION_UI!=='1','Explicit real model/public dependency download component trial.');
  test.setTimeout(240000);
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');expect((await page.request.get('/session')).ok()).toBe(true);
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await page.locator('.settings-modal select').selectOption(model);
  await expect.poll(async()=>(await (await page.request.get('/settings')).json()).default_model).toBe(model);
  await page.locator('.settings-modal .modal-heading button').click();
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await page.getByRole('combobox',{name:'工作方式',exact:true}).selectOption('H1');
  await page.getByRole('combobox',{name:'任务工具范围',exact:true}).selectOption('custom');
  for(const label of ['获取公开依赖','读回产物'])await page.getByLabel(label,{exact:true}).check();
  await page.locator('.composer textarea').fill(`软件组件测试：实际调用acquire_dependencies(wheels=[${JSON.stringify(wheel)}],source="tuna")。这是私密上下文，先提出审批并等待用户；批准后使用当前匹配的approval_id实际下载一次。成功后实际read_artifact读取lock_artifact_id，再final_result引用真实wheel与锁文件ID，用中文说明已下载和核验、尚未安装或证明科学精度。仅做依赖获取，不安装、执行代码或联网搜索。不要重复已成功下载。`);
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/tasks');
  await page.locator('.send-button').click();
  const response=await created;expect(response.status()).toBe(202);const taskId=(await response.json()).task_id;
  test.info().annotations.push({type:'acquisition_task_id',description:taskId});
  trials.set(test.info().title,taskId);
  let approvals:any[]=[];
  await expect.poll(async()=>{approvals=await(await page.request.get(`/tasks/${taskId}/approvals`)).json();
    return approvals.some(a=>a.request.kind==='environment.acquire'&&a.status==='pending');},{timeout:80000}).toBe(true);
  const approval=approvals.find(a=>a.status==='pending');
  expect(JSON.parse(approval.request.value)).toEqual({wheels:[wheel],source:'tuna'});
  expect(approval.request.metadata_urls).toEqual(['https://pypi.org/pypi/six/1.17.0/json']);
  const panel=page.getByLabel('网络请求审批');
  await expect(panel).toContainText('公开依赖获取');await expect(panel).toContainText('全部官方元数据地址');
  await page.screenshot({path:`../workspace/evidence/workbench-acquisition-${model}-${taskId}-desktop.png`,fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`../workspace/evidence/workbench-acquisition-${model}-${taskId}-mobile.png`,fullPage:true});
  await panel.getByRole('button',{name:'批准这项请求',exact:true}).click();
  await expect.poll(async()=>{
    const grants=await(await page.request.get(`/tasks/${taskId}/approvals`)).json();
    const current=await(await page.request.get(`/tasks/${taskId}`)).json();
    return grants.some((a:any)=>a.status==='consumed')?current.status:['paused','failed'].includes(current.status)?current.status:'approval_pending';},
    {timeout:100000,intervals:[1000,2000]}).toMatch(/^(waiting_user|paused|failed)$/);
  const state=await(await page.request.get(`/tasks/${taskId}`)).json();
  approvals=await(await page.request.get(`/tasks/${taskId}/approvals`)).json();
  const events:any[]=[];let cursor=0;
  for(let n=0;n<100;n++){
    const batch=await(await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;events.push(...batch);cursor=batch.at(-1).sequence;
  }
  const artifacts=await(await page.request.get(`/tasks/${taskId}/artifacts`)).json();
  const evidence={model,task_id:taskId,state,approvals,events,artifacts,all_passed:false,
    scope:'real model download and approval component; installation tested separately',general_stage_acceptance:'incomplete',scientific_accuracy:'not_assessed'};
  const evidencePath=`../workspace/evidence/ui-acquisition-${model}-${taskId}.json`;
  writeFileSync(evidencePath,JSON.stringify(evidence,null,2));
  expect(state.status).toBe('waiting_user');expect(state.input_refs).toEqual([]);
  expect(approvals).toHaveLength(1);expect(approvals[0].status).toBe('consumed');
  const receipt=events.find(e=>e.type==='action.observed'&&e.payload.tool==='environment.acquire'&&e.payload.result.wheel_artifact_ids)?.payload.result;
  expect(receipt).toBeTruthy();expect(receipt.wheels).toHaveLength(1);
  const archived=artifacts.find((a:any)=>a.artifact_id===receipt.wheel_artifact_ids[0]);expect(archived.name).toBe(wheel.filename);
  const bytes=await(await page.request.get(archived.download_url)).body();
  expect(bytes.byteLength).toBe(receipt.wheels[0].size_bytes);
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(receipt.wheels[0].sha256);
  expect(events.some(e=>e.type==='action.observed'&&e.payload.tool==='artifact.read'&&e.payload.result.kind==='completed'&&e.payload.result.artifact_id===receipt.lock_artifact_id)).toBe(true);
  expect(events.filter(e=>e.type==='action.started'&&e.payload.tool==='environment.acquire')).toHaveLength(2);
  expect(events.some(e=>e.type==='action.started'&&['web.search','web.read','environment.prepare','code.execute','role.delegate'].includes(e.payload.tool))).toBe(false);
  expect(errors).toEqual([]);
  writeFileSync(evidencePath,JSON.stringify({...evidence,all_passed:true},null,2));
});
