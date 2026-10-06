import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('real workbench navigation, diagnostics and responsive layout', async ({ page }) => {
  const errors:string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'从一个目标，开始工作。'})).toBeVisible();
  await page.getByRole('button',{name:'新建任务'}).click();
  await expect(page.getByRole('heading',{name:'你希望完成什么？'})).toBeVisible();
  await page.getByRole('button',{name:'整理文件与证据'}).click();
  await expect(page.locator('textarea')).toHaveValue(/Markdown/);
  await page.getByRole('button',{name:'添加本地数据'}).click();
  await expect(page.getByRole('heading',{name:'登记本地数据源'})).toBeVisible();
  await page.getByRole('button',{name:'返回',exact:true}).click();
  await page.getByRole('button',{name:'模型与环境设置'}).click();
  await expect(page.getByRole('heading',{name:'模型与执行环境'})).toBeVisible();
  await expect(page.getByText('qwen3.5:4b',{exact:true})).toBeVisible({timeout:30000});
  await page.locator('.settings-modal .modal-heading button').click();
  await page.screenshot({path:'../workspace/evidence/workbench-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('heading',{name:'从一个目标，开始工作。'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({path:'../workspace/evidence/workbench-mobile.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('server rejects invalid source path with a visible explanation', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button',{name:'添加本地数据'}).click();
  await page.getByLabel('本地路径').fill('C:\\nonexistent-forestry-ui-fixture-20261006');
  await page.getByRole('button',{name:'登记只读资源'}).click();
  await expect(page.getByRole('alert')).toContainText('选择的本地路径不存在');
  await expect(page.getByRole('alert')).toBeVisible();
});

test('long-term memory is explicitly saved, survives reload and can be disabled', async ({page}) => {
  const note = `浏览器记忆夹具 ${Date.now()}：报告使用中文。`;
  await page.goto('/');
  await page.getByRole('button',{name:'模型与环境设置'}).click();
  await page.getByLabel('希望长期记住的内容').fill(note);
  await page.getByRole('button',{name:'明确保存这条记忆'}).click();
  await expect(page.locator('.memory-list')).toContainText(note);
  await page.screenshot({path:'../workspace/evidence/workbench-memory.png',fullPage:true});
  await page.reload();
  await page.getByRole('button',{name:'模型与环境设置'}).click();
  const row = page.locator('.memory-list>div').filter({hasText:note});
  await expect(row).toBeVisible();
  await row.getByTitle('停用这条记忆').click();
  await expect(page.locator('.memory-list')).not.toContainText(note);
});

test('real model reads a registered source and seals only after explicit acceptance', async ({page}) => {
  test.setTimeout(180000);
  const source = resolve('../fixtures/general/ui-heights.csv');
  const original = 'tree_id,height_m\nT001,8.25\nT002,12.50\nT003,16.75\n';
  writeFileSync(source,original);
  await page.goto('/');
  await page.getByRole('button',{name:'新建任务'}).click();
  await page.getByRole('button',{name:'添加本地数据'}).click();
  await page.getByLabel('本地路径').fill(source);
  await page.getByRole('button',{name:'登记只读资源'}).click();
  await expect(page.getByRole('heading',{name:'登记本地数据源'})).not.toBeVisible();
  await page.locator('textarea').fill('实际读取已选 ui-heights.csv，计算记录数、height_m 总和与算术平均值。保存 ui-result.json，字段必须是 tree_count、total_height_m、mean_height_m；另保存 ui-report.md，使用中文说明输入来源、结果与没有现场精度验证。检查产物并返回这两个完整 artifact_ids。不要执行代码。');
  await page.getByTitle('发送任务',{exact:true}).click();
  await expect(page.getByRole('button',{name:'检查并验收结果'})).toBeVisible({timeout:120000});
  const taskId = await page.evaluate(()=>localStorage.getItem('forestry.task'));
  const task = await (await page.request.get(`/tasks/${taskId}`)).json();
  expect(task.status).toBe('waiting_user');
  const artifacts = await (await page.request.get(`/tasks/${taskId}/artifacts`)).json();
  const numeric = artifacts.find((a:any)=>a.name==='ui-result.json');
  expect(numeric).toBeTruthy();
  expect(await (await page.request.get(numeric.preview_url)).json()).toEqual({tree_count:3,total_height_m:37.5,mean_height_m:12.5});
  expect(readFileSync(source,'utf8')).toBe(original);
  await page.getByRole('button',{name:'检查并验收结果'}).click();
  await expect(page.getByRole('heading',{name:'验收本次结果'})).toBeVisible();
  await expect(page.getByText('不代表已验证科学精度',{exact:false})).toBeVisible();
  await page.screenshot({path:'../workspace/evidence/workbench-acceptance.png',fullPage:true});
  await page.getByLabel('验收意见或需要修改的内容').fill('自动化验收夹具：数值由独立已知值核对通过。');
  await page.getByRole('button',{name:'确认满足目标并封存'}).click();
  await expect(page.locator('.control-card strong')).toHaveText('已完成');
  const final = await (await page.request.get(`/tasks/${taskId}`)).json();
  const manifest = await (await page.request.get(`/artifacts/${final.delivery.manifest_artifact_id}/content`)).json();
  expect(manifest.acceptance.scientific_accuracy).toBe('not_assessed');
  expect(manifest.candidate.outputs.length).toBe(2);
  await page.screenshot({path:'../workspace/evidence/workbench-completed.png',fullPage:true});
});
