import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('deployed native thinking can be reopened without generating another answer',async({page})=>{
  test.skip(process.env.RUN_NATIVE_THINKING_DEPLOYED!=='1','Read-only check of existing real response.');
  const proof=JSON.parse(readFileSync(resolve('../workspace/evidence/native-thinking-real-20261014-v1.json'),'utf8'));
  expect(test.info().project.use.baseURL).toBe(proof.deployment.url);
  const reply=proof.after.messages.at(-1);
  expect(proof.new_requests).toBe(1);expect(proof.new_actions).toBe(0);
  expect(reply.thinking.length).toBeGreaterThan(0);
  await page.addInitScript(taskId=>localStorage.setItem('forestry.task',taskId),proof.after.task_id);
  await page.goto('/');
  const message=page.locator('.message.assistant').last();
  await expect(message.locator('.message-text')).toContainText(reply.text.split('\n')[0]);
  const thought=message.getByLabel('模型思考');
  if(await thought.getAttribute('open')===null)await thought.locator('summary').click();
  await expect.poll(()=>thought.locator('.thinking-text').textContent()).toBe(reply.thinking);
  await expect(page.getByRole('button',{name:'任务',exact:true})).toHaveCount(0);
  await expect(page.getByText('处理说明',{exact:true})).toHaveCount(0);
  await expect(page.getByText('处理过程',{exact:true})).toHaveCount(0);
  await expect(page.getByText('本次结果说明',{exact:true})).toHaveCount(0);
  await thought.scrollIntoViewIfNeeded();
  await page.screenshot({path:resolve('../workspace/evidence/native-thinking-deployed-20261014.png'),fullPage:true});
  writeFileSync(resolve('../workspace/evidence/native-thinking-deployed-ui-20261014.json'),JSON.stringify({status:'passed',
    mode:'read_only',task_id:proof.after.task_id,thinking_length:reply.thinking.length,new_model_requests:0},null,2));
});
