import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('actual main entry restores the real task, previews and downloads preserved report bytes',async({page})=>{
  const report=JSON.parse(readFileSync(resolve('../workspace/report-acceptance-20261026/static/report.json'),'utf8'));
  const taskId=report.task_id,artifactId=report.state.artifact_ids[0];
  expect(taskId).toBe('efcbe703-3234-459e-a720-caea008eff03');
  const errors:string[]=[],mutations:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))mutations.push(`${r.method()} ${new URL(r.url()).pathname}`);});
  await page.goto('/');
  const history=page.locator(`.sidebar .history-row[data-task-id="${taskId}"] .history-item`);
  await expect(history).toBeVisible();
  await history.click();
  await expect(page.locator('.message.assistant').last()).toContainText('报告下载链接');
  await expect(page.locator('.message.assistant').last().locator('table')).toHaveCount(1);
  await expect(page.locator('.task-status')).toHaveText('已回复');
  await page.getByRole('button',{name:'verification_report.md',exact:true}).click();
  await expect(page.locator('.preview-modal pre')).toContainText('EPSG:32650');
  await expect(page.locator('.preview-modal pre')).toContainText('4,935,083');
  const download=page.locator('.preview-modal').getByRole('link',{name:'下载完整文件'});
  await expect(download).toHaveAttribute('href',`/artifacts/${artifactId}/content?download=true`);
  const response=await page.request.get(await download.getAttribute('href') as string);
  expect(response.status()).toBe(200);
  const bytes=await response.body();
  expect(bytes.length).toBe(3382);
  expect(createHash('sha256').update(bytes).digest('hex')).toBe('cb121129359af368991675400fa181121790f0b515c692bc56b6c91c5c9e4c20');
  await page.locator('.preview-modal .modal-heading button').click();
  await page.getByRole('button',{name:'新建会话',exact:true}).click();
  await expect(page.locator('.message.assistant')).toHaveCount(0);
  await history.click();
  await page.reload();
  await expect(page.locator('.message.assistant').last()).toContainText('报告下载链接');
  await expect(page.getByRole('button',{name:'verification_report.md',exact:true})).toBeVisible();
  await page.locator('.composer textarea').fill('主入口检查草稿，不发送、不追加模型请求');
  await expect(page.locator('.send-button')).toBeEnabled();
  const evidence=process.env.FORESTRY_REPORT_UI_EVIDENCE||'20261026';
  await page.screenshot({path:resolve(`../workspace/evidence/report-main-${evidence}-desktop.png`),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await expect(page.getByRole('button',{name:'打开任务与资源'})).toBeVisible();
  await page.screenshot({path:resolve(`../workspace/evidence/report-main-${evidence}-mobile.png`),fullPage:true});
  const state=await(await page.request.get(`/tasks/${taskId}`)).json();
  expect(state.model_requests).toBe(report.state.model_requests);
  expect(state.action_count).toBe(report.state.action_count);
  expect(errors).toEqual([]);expect(mutations).toEqual([]);
  writeFileSync(resolve(`../workspace/evidence/report-main-${evidence}-verification.json`),JSON.stringify({
    status:'passed',task_id:taskId,artifact_id:artifactId,api:'actual backend',ui:'bundled main entry',
    model_dispatch_enabled:false,new_model_requests:0,mutations,page_errors:errors,
    report_semantic_acceptance:'failed; only original saved bytes viewed, not approved'},null,2));
});
