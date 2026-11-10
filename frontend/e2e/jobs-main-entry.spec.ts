import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('actual main entry displays archived real executor Jobs, logs and the original result',async({page})=>{
  const taskId='ec6f8c6d-12ab-4205-9298-899c2da53f06';
  const errors:string[]=[],mutations:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))mutations.push(r.method()+' '+new URL(r.url()).pathname);});
  await page.goto('/');
  await page.locator(`.sidebar .history-row[data-task-id="${taskId}"] .history-item`).click();
  await page.getByRole('button',{name:'后台任务',exact:true}).click();
  const completed=page.locator('.jobs-panel article').filter({hasText:'c948ff3a-d70c-4f61-8128-d0adf6c4cbec'});
  const cancelled=page.locator('.jobs-panel article').filter({hasText:'04e14c79-67a3-4c7b-a0eb-cfa4c49bfcaa'});
  await expect(completed.getByRole('heading')).toHaveText('执行完成 · 代码作业');
  await expect(cancelled.getByRole('heading')).toHaveText('已取消 · 代码作业');
  await expect(page.getByRole('button',{name:'取消作业',exact:true})).toHaveCount(0);
  await completed.getByText('日志与执行详情',{exact:true}).click();
  await completed.getByRole('button',{name:'查看日志',exact:true}).click();
  await expect(completed.locator('pre')).toContainText('restart seam started');
  const result=completed.getByRole('link',{name:'resume-proof.txt',exact:true});
  const response=await page.request.get(await result.getAttribute('href') as string);
  expect(response.status()).toBe(200);
  expect(createHash('sha256').update(await response.body()).digest('hex')).toBe('5dd3f791590841e94a67f5012969b7148ff0a3ea56a90777ef36837f14b5b076');
  await page.screenshot({path:resolve('../workspace/evidence/jobs-main-20261026.png'),fullPage:true});
  await page.reload();
  await page.getByRole('button',{name:'后台任务',exact:true}).click();
  await expect(page.locator('.jobs-panel article')).toHaveCount(2);
  const state=await(await page.request.get(`/tasks/${taskId}`)).json();
  expect(state.model_requests).toBe(0);expect(state.action_count).toBe(2);
  const jobs=await(await page.request.get(`/tasks/${taskId}/jobs`)).json();
  expect(jobs.jobs.map((j:any)=>j.status).sort()).toEqual(['cancelled','succeeded']);
  expect(errors).toEqual([]);expect(mutations).toEqual([]);
  writeFileSync(resolve('../workspace/evidence/jobs-main-20261026-verification.json'),JSON.stringify({
    status:'passed',task_id:taskId,jobs:jobs.jobs.map((j:any)=>({job_id:j.job_id,status:j.status})),
    api:'actual backend',ui:'bundled main entry',new_jobs:0,new_model_requests:0,mutations,page_errors:errors,
    scope:'read archived original executor evidence, not a repeated Docker run or new autonomous task'},null,2));
});
