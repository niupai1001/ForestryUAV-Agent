import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

test.use({actionTimeout:15000});

test('session sources and products stay isolated; advanced controls live in settings', async ({page}) => {
  test.skip(process.env.RUN_SESSION_UI !== '1', 'Explicit isolated UI fixture; no real model trial.');
  test.setTimeout(60000);
  const fixture=JSON.parse(readFileSync(resolve('../workspace/session-ui-delivery-20261013-v1/metadata.json'),'utf8'));
  expect(fixture.enable_agent).toBe(false);
  expect(new URL(test.info().project.use.baseURL as string).origin).toBe(fixture.url);
  const seed=fixture.seed, errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  const settings=page.getByRole('dialog',{name:'Agent 设置'});
  const sourceDialog=page.getByRole('dialog',{name:'会话数据源'});
  const sources=page.getByRole('region',{name:'会话数据源列表'});
  let taskC:any;
  if(process.env.SESSION_UI_CONTINUE_TASK){
    const id=process.env.SESSION_UI_CONTINUE_TASK;
    await page.request.get('/session');
    taskC=await (await page.request.get(`/tasks/${id}`)).json();
    const activeId=process.env.SESSION_UI_CONTINUE_STAGE==='after_switch'?seed.B.task_id:id;
    await page.addInitScript(value=>{if(!localStorage.getItem('forestry.task'))localStorage.setItem('forestry.task',value);},activeId);
    await page.goto('/');
    await expect(page.locator('.user-message')).toContainText(process.env.SESSION_UI_CONTINUE_STAGE==='after_switch'?seed.B.objective:taskC.objective);
  }else{
  await page.goto('/');
  await expect(page.locator('.composer select')).toHaveCount(0);
  await expect(page.locator('.source-chips')).toHaveCount(0);
  await expect(page.locator('.composer')).not.toContainText(seed.orphan.name);
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await settings.getByText('Agent 高级设置',{exact:true}).click();
  await settings.getByRole('combobox',{name:'工作方式',exact:true}).selectOption('H1');
  await settings.getByRole('combobox',{name:'任务工具范围',exact:true}).selectOption('files');
  await settings.getByRole('button',{name:'关闭设置',exact:true}).click();
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await page.reload();
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await settings.getByText('Agent 高级设置',{exact:true}).click();
  await expect(settings.getByRole('combobox',{name:'工作方式',exact:true})).toHaveValue('H1');
  await expect(settings.getByRole('combobox',{name:'任务工具范围',exact:true})).toHaveValue('files');
  await settings.getByRole('button',{name:'关闭设置',exact:true}).click();
  await page.getByRole('button',{name:'添加本地数据',exact:true}).click();
  await expect(sourceDialog.getByRole('combobox',{name:'浏览来源'}).locator('option')).toHaveCount(1);
  await sourceDialog.getByLabel('本地路径').fill(seed.draft.path);
  const registered=page.waitForResponse(response=>new URL(response.url()).pathname==='/resources/register');
  await sourceDialog.getByRole('button',{name:'登记只读资源',exact:true}).click();
  const draftSource=await (await registered).json();
  await expect(page.locator('.source-chips')).toContainText(seed.draft.name);
  await expect(page.locator('.source-chips')).not.toContainText(seed.A.name);
  await page.locator('.composer textarea').fill('会话C：仅核验当前输入关联，不运行模型');
  const created=page.waitForResponse(response=>new URL(response.url()).pathname==='/tasks'&&response.request().method()==='POST');
  await page.locator('.send-button').click();
  const receipt=await (await created).json();
  taskC=await (await page.request.get(`/tasks/${receipt.task_id}`)).json();
  expect(taskC.input_refs).toEqual([draftSource.resource_id]);
  expect(taskC.config_snapshot.harness_mode).toBe('H1');
  expect(taskC.constraints.allowed_capabilities).not.toContain('code.execute');
  }
  if(process.env.SESSION_UI_CONTINUE_STAGE!=='after_switch'){
  await page.getByRole('button',{name:/^数据源\s*\d*$/}).click();
  await expect(sources).toContainText(seed.draft.name);
  await expect(sources).not.toContainText(seed.A.name);
  await expect(sources).not.toContainText(seed.B.name);
  await page.locator('.history-item').filter({hasText:seed.A.objective}).click();
  await expect(sources).toContainText(seed.A.name);
  await expect(sources).not.toContainText(seed.draft.name);
  await page.getByRole('button',{name:/^产物\s*\d*$/}).click();
  await expect(page.locator('.artifact-list')).toContainText(seed.A.artifact_name);
  await page.route(`**/tasks/${seed.B.task_id}/artifacts`,async route=>{
    await new Promise(resolve=>setTimeout(resolve,250));await route.continue();
  });
  await page.locator('.history-item').filter({hasText:seed.B.objective}).click();
  await expect(page.locator('.artifact-list')).not.toContainText(seed.A.artifact_name);
  await expect(page.locator('.artifact-list')).toContainText(seed.B.artifact_name);
  }
  await page.reload();
  await expect(page.locator('.user-message')).toContainText(seed.B.objective);
  await page.getByRole('button',{name:/^数据源\s*\d*$/}).click();
  await expect(sources).toContainText(seed.B.name);
  await expect(sources).not.toContainText(seed.A.name);
  await page.getByRole('button',{name:'添加或浏览数据源',exact:true}).click();
  await expect(sourceDialog.getByRole('combobox',{name:'浏览来源'}).locator('option')).toHaveCount(2);
  await expect(sourceDialog).not.toContainText(seed.A.name);
  await sourceDialog.getByRole('button',{name:'关闭本地数据源'}).click();
  await page.locator('.history-item').filter({hasText:seed.A.objective}).click();
  await expect(sources).toContainText(seed.A.name);
  await page.getByRole('button',{name:'添加或浏览数据源',exact:true}).click();
  await sourceDialog.getByLabel('本地路径').fill(seed.added.path);
  const added=page.waitForResponse(response=>new URL(response.url()).pathname==='/resources/register');
  await sourceDialog.getByRole('button',{name:'登记只读资源',exact:true}).click();
  const addedSource=await (await added).json();
  const amendment=page.getByRole('dialog',{name:'修改任务规范'});
  await expect(amendment.getByRole('combobox')).toHaveCount(0);
  await expect(amendment).not.toContainText(seed.B.name);
  await amendment.getByLabel(seed.added.name,{exact:true}).uncheck();
  await amendment.getByLabel(seed.added.name,{exact:true}).check();
  await amendment.getByRole('button',{name:'保存规范修订'}).click();
  await expect(sources).toContainText(seed.added.name);
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await settings.getByText('Agent 高级设置',{exact:true}).click();
  await settings.getByRole('combobox',{name:'本会话工具范围',exact:true}).selectOption('answer');
  await settings.getByRole('button',{name:'保存本会话工具范围'}).click();
  await expect(settings).not.toBeVisible();
  const revised=await (await page.request.get(`/tasks/${seed.A.task_id}`)).json();
  expect(revised.input_refs).toEqual([seed.A.resource_id,addedSource.resource_id]);
  expect(revised.constraints.allowed_capabilities).toEqual([]);
  expect(revised.config_snapshot.harness_mode).toBe('H2');
  expect(revised.status).toBe('paused');
  await page.screenshot({path:'../workspace/evidence/session-settings-desktop-20261013-v1.png',fullPage:true});
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await expect(page.locator('.source-chips')).toHaveCount(0);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'../workspace/evidence/session-settings-mobile-20261013-v1.png',fullPage:true});
  expect(errors).toEqual([]);
  writeFileSync(resolve('../workspace/evidence/session-settings-20261013-v1.json'),JSON.stringify({
    passed:true,fixture:'isolated_ui_no_model',continued_from_task:process.env.SESSION_UI_CONTINUE_TASK||null,
    task_a:seed.A.task_id,task_b:seed.B.task_id,task_c:taskC.task_id,
    revised_input_refs:revised.input_refs,model_calls:0,scientific_jobs:0,checks:[
      'advanced controls only in settings','preferences persist for new sessions','new session excludes historical sources',
      'registered draft source attached only to created session','sources and products isolated on switch and reload',
      'source browser and amendment exclude other sessions','input amendment preserves permissions',
      'scope setting preserves input refs and harness','mobile width fits','no browser errors']},null,2));
});
