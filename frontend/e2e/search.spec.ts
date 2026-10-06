import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

for(const model of ['small','large']) test(`${model} performs public search and primary-source reporting through the workbench`, async ({page}) => {
  test.skip(!process.env.RUN_SEARCH_UI, 'Set RUN_SEARCH_UI=1 to run real external search/model work.');
  test.setTimeout(390000);
  const errors:string[] = [];
  page.on('pageerror', error=>errors.push(error.message));
  await page.goto('/');
    await page.getByRole('button',{name:'模型与环境设置'}).click();
    await page.locator('.settings-modal select').selectOption(model);
    await expect.poll(async()=> (await (await page.request.get('/settings')).json()).default_model).toBe(model);
    await page.locator('.settings-modal .modal-heading button').click();
    await page.getByRole('button',{name:'新建任务'}).click();
    await page.getByLabel('工作方式',{exact:true}).selectOption('H1');
    await page.getByRole('checkbox',{name:'这项任务的目标、补充消息和输入均为公开信息'}).check();
    await page.locator('.composer textarea').fill('这是公开搜索与报告的软件验收任务，不含私密数据。实际search_web查询“Python json documentation”，从真实结果选择docs.python.org的json库官方页面，逐字复制结果URL再read_web_page读取原文，不改变链接语言或版本路径，不能猜链接或仅用摘要。根据原文说明json.loads与json.dumps的作用。write_file保存中文ui-search-report.md，包含两个函数的输入和输出，并用citation_actions同时绑定citation到真实网页读取action_id、search_citation到真实搜索action_id，自动附加真实URL、source_sha256、snapshot_artifact_id及搜索快照snapshot_artifact_id；使用“来源”和“局限”标题，说明示例未运行。保存后实际read_artifact读回，再final_result引用完整产物ID。不执行代码、不委派、不自动验收，失败则如实停止。');
    const created = page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/tasks');
    await page.locator('.send-button').click();
    const response = await created;
    expect(response.ok()).toBe(true);
    const initial = await response.json();
    const taskId = initial.task_id;
    await expect.poll(async()=> (await (await page.request.get(`/tasks/${taskId}`)).json()).status,
      {timeout:320000,intervals:[1000,2000]}).toMatch(/^(waiting_user|paused|failed|cancelled|completed)$/);
    const state = await (await page.request.get(`/tasks/${taskId}`)).json();
    const events:any[] = [];
    let cursor = 0;
    for(let pageNumber=0;pageNumber<100;pageNumber++) {
      const batch = await (await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
      if(!batch.length)break;
      events.push(...batch); cursor=batch[batch.length-1].sequence;
    }
    const artifacts = await (await page.request.get(`/tasks/${taskId}/artifacts`)).json();
    const evidencePath = `../workspace/evidence/ui-search-${model}-${taskId}.json`;
    const evidence = {schema_version:1, task_id:taskId, model, state, events, artifacts,
      all_passed:false, general_stage_acceptance:'incomplete',code_execution:'not_run',scientific_accuracy:'not_assessed'};
    writeFileSync(evidencePath,JSON.stringify(evidence,null,2));
    expect(state.status).toBe('waiting_user');
    await expect(page.locator('.task-status')).toHaveText('待确认');
    expect(state.model_profile).toBe(model);
    expect(state.config_snapshot.context_classification).toBe('public');
    expect(state.config_snapshot.search.provider).toBe('baidu_html');
    const search = events.find(event=>event.type==='action.observed'&&event.payload.tool==='web.search'&&event.payload.result.kind==='completed');
    const read = events.find(event=>event.type==='action.observed'&&event.payload.tool==='web.read'&&event.payload.result.kind==='completed');
    expect(search).toBeTruthy();expect(read).toBeTruthy();
    expect(read.sequence).toBeGreaterThan(search.sequence);
    expect(new URL(read.payload.result.citation.url).hostname).toBe('docs.python.org');
    expect(search.payload.result.results.some((result:{url:string})=>result.url===read.payload.result.citation.url)).toBe(true);
    for(const citation of [search.payload.result.citation, read.payload.result.citation]) {
      const snapshot = artifacts.find((artifact:{artifact_id:string})=>artifact.artifact_id===citation.snapshot_artifact_id);
      expect(snapshot).toBeTruthy();
      const raw = await (await page.request.get(snapshot.download_url)).body();
      expect(createHash('sha256').update(raw).digest('hex')).toBe(citation.source_sha256);
    }
    const report = artifacts.findLast((artifact:{name:string})=>artifact.name==='ui-search-report.md');
    expect(report).toBeTruthy();
    await page.getByRole('button',{name:'ui-search-report.md',exact:true}).last().click();
    const preview = page.locator('.preview-modal pre');
    for(const value of ['json.loads','json.dumps','来源','局限',read.payload.result.citation.url,
      read.payload.result.citation.source_sha256,read.payload.result.citation.snapshot_artifact_id,search.payload.result.citation.snapshot_artifact_id]) {
      await expect(preview).toContainText(value);
    }
    expect(events.some(event=>event.type==='action.started'&&event.payload.tool==='artifact.read'&&event.payload.arguments.artifact_id===report.artifact_id)).toBe(true);
    await page.screenshot({path:`../workspace/evidence/workbench-search-${model}.png`,fullPage:true});
    await page.locator('.preview-modal .modal-heading button').click();
    await page.getByRole('button',{name:'执行记录',exact:true}).click();
    await expect(page.locator('.trace-list')).toContainText('web.search');
    await expect(page.locator('.trace-list')).toContainText('web.read');
    await page.reload();
    await expect(page.locator('.task-meta')).toContainText('公开上下文');
    await expect(page.getByRole('button',{name:'ui-search-report.md',exact:true}).last()).toBeVisible();
    expect(errors).toEqual([]);
    writeFileSync(evidencePath,JSON.stringify({...evidence, all_passed:true,
      checks:'UI create, provider/model freeze, ordered real search/read, result-selected source, raw hashes, report/citations/read-back, trace and reload verified',
      general_stage_acceptance:'incomplete',code_execution:'not_run',scientific_accuracy:'not_assessed'
    },null,2));
});
