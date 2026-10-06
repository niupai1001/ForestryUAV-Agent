import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot=fileURLToPath(new URL('../../',import.meta.url));
const evidencePath=(name:string)=>resolve(repoRoot,'workspace/evidence',name);

test.use({actionTimeout:15000});

for(const model of ['small','large']) test(`${model} creates and revises task tool scope through the workbench`,async({page})=>{
  test.skip(process.env.RUN_SCOPE_UI!=='1','Explicit real local model UI trial.');
  test.setTimeout(240000);
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const original=readFileSync(resolve(repoRoot,'fixtures/general/heights.csv'));
  const fixtureName=`scope-ui-heights-${model}-${crypto.randomUUID().slice(0,8)}.csv`;
  const sourcePath=resolve(repoRoot,`fixtures/general/${fixtureName}`);
  writeFileSync(sourcePath,original);
  await page.goto('/');
  expect((await page.request.get('/session')).ok()).toBe(true);
  const grant=await page.request.post('/resources/register',{headers:{origin:new URL(page.url()).origin},data:{request_id:crypto.randomUUID(),
    path:sourcePath,classification:'public',read_only:true}});
  expect(grant.ok(),await grant.text()).toBe(true);
  const resource=await grant.json();
  await page.reload();
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await page.locator('.settings-modal select').selectOption(model);
  await expect.poll(async()=>(await (await page.request.get('/settings')).json()).default_model).toBe(model);
  await page.locator('.settings-modal .modal-heading button').click();
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await page.getByRole('combobox',{name:'工作方式',exact:true}).selectOption('H1');
  await page.getByRole('combobox',{name:'任务工具范围',exact:true}).selectOption('answer');
  await page.locator('.composer textarea').fill('请回答2加2的结果，给出final_result，不生成文件，不执行工具，不自动验收。');
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/tasks');
  await page.locator('.send-button').click();
  const response=await created;expect(response.status()).toBe(202);
  const initial=await response.json(),taskId=initial.task_id;
  test.info().annotations.push({type:'scope_task_id',description:taskId});
  await expect.poll(async()=>(await (await page.request.get(`/tasks/${taskId}`)).json()).status,
    {timeout:70000,intervals:[500,1000]}).toBe('waiting_user');
  const before=await (await page.request.get(`/tasks/${taskId}`)).json();
  expect(before.constraints.allowed_capabilities).toEqual([]);
  expect(before.action_count).toBe(0);expect(before.model_profile).toBe(model);
  expect(before.messages.filter((m:{role:string})=>m.role==='assistant').map((m:{text:string})=>m.text).join('\n')).toMatch(/4|四/);
  await expect(page.locator('.user-message')).toContainText('任务工具范围：仅回答');
  await page.getByRole('button',{name:'修改目标、输入与工具范围',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'修改任务规范'});
  await dialog.getByRole('textbox',{name:'修订任务目标'}).fill('请实际读取已授权的CSV，计算记录数、height_m总和与平均值。写scope-result.json，字段tree_count、total_height_m、mean_height_m都是数值；再写中文scope-report.md说明来源和结果、属于软件夹具而不是现场科学精度。实际read_artifact读回两个文件，final_result引用真实产物ID，等待我验收。');
  await dialog.getByLabel(fixtureName,{exact:true}).check();
  await dialog.getByRole('combobox',{name:'修订工具范围',exact:true}).selectOption('files');
  await expect(dialog.getByLabel('执行受限代码',{exact:true})).not.toBeChecked();
  await expect(dialog.getByLabel('委派角色',{exact:true})).not.toBeChecked();
  await page.screenshot({path:evidencePath(`workbench-scope-amend-${model}.png`),fullPage:true});
  const amended=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/tasks/${taskId}/amendments`);
  await dialog.getByRole('button',{name:'保存规范修订',exact:true}).click();
  expect((await amended).ok()).toBe(true);
  await expect(page.locator('.task-status')).toHaveText('已暂停');
  await page.reload();
  await expect(page.locator('.user-message')).toContainText('修订 2');
  await expect(page.locator('.user-message')).toContainText('任务工具范围：文件与报告');
  const revised=await (await page.request.get(`/tasks/${taskId}`)).json();
  expect(revised.input_refs).toEqual([resource.resource_id]);
  expect(revised.model_profile).toBe(model);
  expect(revised.constraints.allowed_capabilities).not.toContain('code.execute');
  const resumed=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/tasks/${taskId}/resume`);
  await page.getByRole('button',{name:'恢复',exact:true}).click();
  const resumeReply=await resumed;expect(resumeReply.ok()).toBe(true);
  const resumeReceipt=await resumeReply.json();expect(resumeReceipt.status).not.toBe('paused');
  let observationError:unknown;
  try { await expect.poll(async()=>(await (await page.request.get(`/tasks/${taskId}`)).json()).status,
    {timeout:130000,intervals:[1000,2000]}).toMatch(/^(waiting_user|paused|failed|cancelled|completed)$/); }
  catch(error){observationError=error;}
  const state=await (await page.request.get(`/tasks/${taskId}`)).json();
  const events:any[]=[];let cursor=0;
  for(let n=0;n<100;n++){
    const batch=await (await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;events.push(...batch);cursor=batch.at(-1).sequence;
  }
  const artifacts=await (await page.request.get(`/tasks/${taskId}/artifacts`)).json();
  const evidence={model,task_id:taskId,before,revised,state,events,artifacts,all_passed:false,
    scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete',observation_timed_out:!!observationError};
  const output=evidencePath(`ui-scope-${model}-${taskId}.json`);
  writeFileSync(output,JSON.stringify(evidence,null,2));
  if(observationError)throw observationError;
  expect(state.status).toBe('waiting_user');expect(state.spec_revision).toBe(2);
  expect(events.some(e=>e.type==='action.started'&&['code.execute','role.delegate','web.search','web.read','memory.read','environment.inspect'].includes(e.payload.tool))).toBe(false);
  const facts=artifacts.findLast((a:{name:string})=>a.name==='scope-result.json');
  const report=artifacts.findLast((a:{name:string})=>a.name==='scope-report.md');
  expect(facts).toBeTruthy();expect(report).toBeTruthy();
  expect(await (await page.request.get(facts.preview_url)).json()).toEqual({tree_count:3,total_height_m:37.5,mean_height_m:12.5});
  for(const artifact of [facts,report])expect(events.some(e=>e.type==='action.started'&&e.payload.tool==='artifact.read'&&e.payload.arguments.artifact_id===artifact.artifact_id)).toBe(true);
  await page.getByRole('button',{name:'scope-report.md',exact:true}).last().click();
  await expect(page.locator('.preview-modal pre')).toContainText('12.5');
  await page.screenshot({path:evidencePath(`workbench-scope-${model}.png`),fullPage:true});
  expect(readFileSync(sourcePath).equals(original)).toBe(true);expect(errors).toEqual([]);
  const settled=await (await page.request.get(`/tasks/${taskId}`)).json();
  const lastEvents=await (await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
  const replayCursor=lastEvents.at(-1)?.sequence??cursor;
  const replay=await page.request.post(`/tasks/${taskId}/resume`,{headers:{origin:new URL(page.url()).origin},
    data:resumeReply.request().postDataJSON()});
  expect(replay.ok()).toBe(true);expect(await replay.json()).toEqual(resumeReceipt);
  expect(await (await page.request.get(`/tasks/${taskId}`)).json()).toEqual(settled);
  expect(await (await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${replayCursor}`)).json()).toEqual([]);
  writeFileSync(output,JSON.stringify({...evidence,all_passed:true,
    checks:'UI creation with no action capabilities; explicit amendment/pause/reload; model/config preserved; exact CSV facts and real artifacts read back; no forbidden actions or source mutation; resumed request replay returns original receipt without new state/events/model turn'},null,2));
});

test.afterEach(async({page},info)=>{
  if(info.status===info.expectedStatus)return;
  const taskId=info.annotations.find(a=>a.type==='scope_task_id')?.description;
  if(!taskId)return;
  const state=await (await page.request.get(`/tasks/${taskId}`)).json();
  const events:any[]=[];let cursor=0;
  for(let n=0;n<100;n++){
    const batch=await (await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;events.push(...batch);cursor=batch.at(-1).sequence;
  }
  writeFileSync(evidencePath(`ui-scope-failure-${taskId}.json`),JSON.stringify({task_id:taskId,state,events,
    all_passed:false,error:info.error?.message,general_stage_acceptance:'incomplete',scientific_accuracy:'not_assessed'},null,2));
});
