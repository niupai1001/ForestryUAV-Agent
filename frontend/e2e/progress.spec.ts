import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

for(const model of ['small','large']) test(`${model} resumes a stalled task only after user correction`, async ({page})=>{
  const taskId = process.env[`PROGRESS_TASK_${model.toUpperCase()}`];
  test.skip(!taskId,'Set PROGRESS_TASK_SMALL/LARGE to real-model paused guard trials.');
  test.setTimeout(390000);
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  const sourceBefore=readFileSync('../fixtures/general/heights.csv');
  await page.goto('/');
  await page.evaluate(id=>localStorage.setItem('forestry.task',id!),taskId);
  await page.reload();
  await expect(page.locator('.task-status')).toHaveText('已暂停');
  await expect(page.locator('.blocker')).toContainText('不会重置计数');
  const before=await (await page.request.get(`/tasks/${taskId}`)).json();
  const refused=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/tasks/${taskId}/resume`);
  await page.getByRole('button',{name:'恢复',exact:true}).click();
  const denied=await refused;
  expect(denied.status()).toBe(422);
  expect((await denied.json()).error.code).toBe('NO_PROGRESS');
  expect((await (await page.request.get(`/tasks/${taskId}`)).json()).model_requests).toBe(before.model_requests);
  await page.locator('.composer textarea').fill('停止重复读取。请从实际CSV计算记录数、height_m总和与算术平均值，保存progress-result.json，字段tree_count、total_height_m、mean_height_m都是数值。另保存中文progress-report.md，说明输入来源和结果、不代表现场精度。实际read_artifact读回这两个文件，final_result引用真实产物ID。不要执行代码、不要联网、不要委派、不要自动验收。');
  const posted=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/tasks/${taskId}/messages`);
  await page.locator('.send-button').click();
  expect((await posted).ok()).toBe(true);
  await expect(page.locator('.task-status')).toHaveText('已暂停');
  const resumed=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/tasks/${taskId}/resume`);
  await page.getByRole('button',{name:'恢复',exact:true}).click();
  expect((await resumed).ok()).toBe(true);
  let observationError:unknown;
  try {
    await expect.poll(async()=>(await (await page.request.get(`/tasks/${taskId}`)).json()).status,
      {timeout:320000,intervals:[1000,2000]}).toMatch(/^(waiting_user|paused|failed|cancelled|completed)$/);
  } catch(error) { observationError=error; }
  const state=await (await page.request.get(`/tasks/${taskId}`)).json();
  const events:any[]=[];
  let cursor=0;
  for(let n=0;n<100;n++) {
    const batch=await (await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;
    events.push(...batch);cursor=batch[batch.length-1].sequence;
  }
  const artifacts=await (await page.request.get(`/tasks/${taskId}/artifacts`)).json();
  const path=`../workspace/evidence/ui-progress-${model}-${taskId}.json`;
  const evidence={task_id:taskId,model,before,state,events,artifacts,all_passed:false,
    observation_timed_out:!!observationError,
    general_stage_acceptance:'incomplete',scientific_accuracy:'not_assessed'};
  writeFileSync(path,JSON.stringify(evidence,null,2));
  if(observationError)throw observationError;
  expect(state.status).toBe('waiting_user');
  expect(state.spec_revision).toBe(before.spec_revision);
  expect(state.model_requests).toBeGreaterThan(before.model_requests);
  expect(state.action_count).toBeGreaterThan(before.action_count);
  expect(events.some(e=>e.type==='action.started'&&['code.execute','role.delegate','web.search','web.read'].includes(e.payload.tool))).toBe(false);
  const superseded=events.find(e=>e.type==='run.superseded');
  expect(superseded?.payload.reason).toBe('user_input_after_no_progress');
  expect(events.some(e=>e.type==='progress.observed'&&e.payload.turn_id===superseded.payload.stopped_turn_id&&e.payload.stalled===3)).toBe(true);
  const facts=artifacts.findLast((a:{name:string})=>a.name==='progress-result.json');
  const report=artifacts.findLast((a:{name:string})=>a.name==='progress-report.md');
  expect(facts).toBeTruthy();expect(report).toBeTruthy();
  expect(await (await page.request.get(facts.preview_url)).json()).toEqual({tree_count:3,total_height_m:37.5,mean_height_m:12.5});
  for(const artifact of [facts,report]) {
    expect(events.some(e=>e.type==='action.started'&&e.payload.tool==='artifact.read'&&e.payload.arguments.artifact_id===artifact.artifact_id)).toBe(true);
  }
  await page.getByRole('button',{name:'progress-report.md',exact:true}).last().click();
  await expect(page.locator('.preview-modal pre')).toContainText('12.5');
  await page.screenshot({path:`../workspace/evidence/workbench-progress-${model}.png`,fullPage:true});
  await page.locator('.preview-modal .modal-heading button').click();
  await page.reload();
  await expect(page.locator('.task-status')).toHaveText('待确认');
  expect(readFileSync('../fixtures/general/heights.csv').equals(sourceBefore)).toBe(true);
  expect(errors).toEqual([]);
  writeFileSync(path,JSON.stringify({...evidence,all_passed:true,
    source_sha256:createHash('sha256').update(sourceBefore).digest('hex'),
    checks:'guard refusal without extra model request, correction queued while paused, explicit UI resume, same revision/cumulative budgets, old stop preserved, exact numbers/report read-back, source/reload verified'},null,2));
});
