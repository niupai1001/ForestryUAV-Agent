import { test,expect } from '@playwright/test';
import { readFileSync,writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('shows waiting until first native thought and keeps latest thought expanded after completion',async({page})=>{
  test.skip(process.env.RUN_RUNTIME_VISIBILITY_UI!=='1','Isolated event fixture; no model.');
  const fixture=JSON.parse(readFileSync(resolve('../workspace/runtime-visibility-ui-20261021/metadata.json'),'utf8'));
  expect(test.info().project.use.baseURL).toBe(fixture.url);expect(fixture.enable_agent).toBe(false);
  await page.addInitScript(id=>localStorage.setItem('forestry.task',id),fixture.task_id);
  await page.goto('/ui/index.html');
  const phase=async(name:string)=>page.evaluate(async(name)=>{const reply=await fetch('/fixture/'+name,{method:'POST'});if(!reply.ok)throw new Error(await reply.text());},name);
  await phase('activate');
  await expect(page.getByRole('status')).toContainText('等待模型输出');
  await expect(page.getByRole('status')).toContainText(/\d+秒/);
  await phase('thinking');
  const thought=page.getByLabel('模型思考');
  await expect(thought).toHaveAttribute('open','');
  await expect(thought).toContainText('模型原生片段一。原生片段二。');
  await expect(page.getByText('等待模型输出',{exact:false})).toHaveCount(0);
  await phase('finish');
  await expect(page.locator('.message.assistant .message-text')).toContainText('这是完整回答。');
  await expect(thought).toHaveAttribute('open','');
  await page.reload();
  await expect(thought).toHaveAttribute('open','');
  await page.screenshot({path:resolve('../workspace/evidence/runtime-visibility-ui-20261021.png'),fullPage:true});
  writeFileSync(resolve('../workspace/evidence/runtime-visibility-ui-20261021.json'),JSON.stringify({status:'passed',real_model_calls:0,enable_agent:false},null,2));
});
