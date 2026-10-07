import { test, expect } from '@playwright/test';
import { writeFileSync } from 'node:fs';

test('native proposed plan and scoped sources survive refresh in desktop and mobile UI', async({page})=>{
  test.skip(process.env.RUN_PLANNING_UI!=='1','Requires an actual completed model planning trial.');
  const taskId=process.env.FORESTRY_PLANNING_TASK;expect(taskId).toBeTruthy();
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  expect((await page.request.get('/session')).ok()).toBe(true);
  const task=await (await page.request.get(`/tasks/${taskId}`)).json();
  const plan=await (await page.request.get(`/tasks/${taskId}/plan`)).json();
  expect(plan.authority).toBe('model_proposed');expect(plan.acceptance).toBe('not_assessed');expect(plan.items.length).toBeGreaterThan(0);
  await page.evaluate(id=>localStorage.setItem('forestry.task',id!),taskId);await page.reload();
  await expect(page.locator('.task-status')).toHaveText('待确认');
  await page.getByRole('button',{name:'计划',exact:true}).click();
  for(const item of plan.items)await expect(page.locator('.plan-list li').filter({hasText:item.content})).toHaveCount(1);
  await expect(page.locator('.plan-list')).toContainText('自报进度');
  expect(await page.locator('.source-chips button').count()).toBe(task.input_refs.length);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`../workspace/evidence/workbench-plan-desktop-${taskId}.png`,fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.reload();
  await page.getByRole('button',{name:'计划',exact:true}).click();
  await expect(page.locator('.plan-list li')).toHaveCount(plan.items.length);
  expect(await page.locator('.source-chips button').count()).toBe(task.input_refs.length);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`../workspace/evidence/workbench-plan-mobile-${taskId}.png`,fullPage:true});
  expect((await (await page.request.get(`/tasks/${taskId}`)).json()).status).toBe(task.status);
  expect(errors).toEqual([]);
  writeFileSync(`../workspace/evidence/ui-planning-${taskId}.json`,JSON.stringify({task_id:taskId,plan,status:task.status,
    scoped_sources:true,desktop_mobile_no_overflow:true,refresh_preserved:true,all_passed:true,
    product_acceptance:'incomplete',scientific_accuracy:'not_assessed'},null,2));
});
