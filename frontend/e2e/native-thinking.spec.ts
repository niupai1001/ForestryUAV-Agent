import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('native thinking stream and history; no synthetic explanations or empty plan',async({page})=>{
  test.skip(process.env.RUN_NATIVE_THINKING_UI!=='1','Only isolated fixture, with no model.');
  const fixture=JSON.parse(readFileSync(resolve('../workspace/native-thinking-ui-20261014/metadata.json'),'utf8'));
  expect(fixture.enable_agent).toBe(false);
  expect(test.info().project.use.baseURL).toBe(fixture.url);
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  const phase=async(name:string)=>page.evaluate(async(name)=>{
    const reply=await fetch('/fixture/'+name,{method:'POST'});
    if(!reply.ok)throw new Error(await reply.text());
  },name);
  await page.goto('/ui/index.html');
  await page.locator('.history-item').filter({hasText:'原生思考流界面夹具'}).click();
  await phase('activate');
  if(process.env.NATIVE_THINKING_RESUME!=='1') {
    await expect(page.getByRole('button',{name:'任务',exact:true})).toHaveCount(0);
    await expect(page.getByText('处理说明',{exact:true})).toHaveCount(0);
    await expect(page.getByText('处理过程',{exact:true})).toHaveCount(0);
    await expect(page.getByText('本次结果说明',{exact:true})).toHaveCount(0);
    await phase('stream');
  }
  const thought=page.getByLabel('模型思考');
  if(process.env.NATIVE_THINKING_RESUME!=='1') {
    await expect(thought).toContainText('原生片段000。');
    await expect(thought).toContainText('原生片段619。');
  }
  await expect(thought).toHaveAttribute('open','');
  await phase('finish');
  await expect(page.locator('.message.assistant').last()).toContainText('完整回答。');
  await expect(page.locator('.message.assistant').last().locator('.message-text')).not.toContainText('原生片段');
  await page.reload();
  await expect(page.getByLabel('模型思考')).not.toHaveAttribute('open','');
  await page.getByLabel('模型思考').locator('summary').click();
  await expect(page.getByLabel('模型思考')).toContainText('原生片段000。');
  await expect(page.getByLabel('模型思考')).toContainText('原生片段619。');
  await phase('plan');
  await expect(page.getByRole('button',{name:'任务',exact:true})).toBeVisible();
  await expect(page.locator('.plan-list')).toContainText('读取输入');
  await expect(page.locator('.plan-list')).toContainText('保存产物');
  expect(errors).toEqual([]);
  await page.screenshot({path:resolve('../workspace/evidence/native-thinking-ui-20261014.png'),fullPage:true});
  writeFileSync(resolve('../workspace/evidence/native-thinking-ui-20261014.json'),JSON.stringify({status:'passed',
    enable_agent:false,task_id:fixture.task_id,page_errors:errors,stream_chunks:620},null,2));
});
