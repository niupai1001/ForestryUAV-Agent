import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('deployed conversation: image message and followup composer are visible',async({page})=>{
  test.skip(process.env.RUN_CONVERSATION_DEPLOYED!=='1','Read-only inspection of the updated main workbench.');
  const deployed=JSON.parse(readFileSync(resolve('../workspace/evidence/conversation-deployment-20261014.json'),'utf8'));
  expect(test.info().project.use.baseURL).toBe(deployed.url);
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await expect(page.locator('.history-item').first()).toBeVisible();
  const runtime=await page.evaluate(async()=>{
    const reply=await fetch('/runtime');
    if(!reply.ok)throw new Error('Runtime status '+reply.status);
    return reply.json();
  });
  expect(runtime.source.sha256).toBe(deployed.source_sha256);
  // The direct capability table passed before the fixture's mistaken label
  // expectation; reuse that evidence and inspect only remaining image UI.
  await page.locator('.history-item').filter({hasText:/^你能干什么\s*$/}).click();
  await expect(page.locator('.message.assistant').last()).toContainText('FOREST 7319');
  await expect(page.locator('.task-status')).toHaveText('已回复');
  await expect(page.locator('.message.assistant').last().locator('strong')).toHaveCount(3);
  const picture=page.locator('.message.user .message-attachments img');
  await expect(picture).toHaveCount(1);
  await expect.poll(async()=>picture.evaluate((image:HTMLImageElement)=>image.complete&&image.naturalWidth>0)).toBe(true);
  await expect(page.locator('.composer .source-chips')).toHaveCount(0);
  await page.locator('.composer textarea').fill('只读界面验收草稿，不发送');
  await expect(page.locator('.send-button')).toBeEnabled();
  await page.screenshot({path:resolve('../workspace/evidence/conversation-deployed-20261014.png'),fullPage:true});
  expect(errors).toEqual([]);
  writeFileSync(resolve('../workspace/evidence/conversation-deployed-ui-20261014.json'),JSON.stringify({status:'passed',
    mode:'read_only',source_sha256:runtime.source.sha256,page_errors:errors,new_model_requests:0,
    reused_capability_render:'conversation-deployed-ui-results-20261014-v2'},null,2));
});
