import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('open long conversation at latest reply and keep older reading position',async({page})=>{
  test.skip(process.env.RUN_CONVERSATION_DEPLOYED!=='1','Read-only check for the newly added scroll behavior.');
  const deployed=JSON.parse(readFileSync(resolve('../workspace/evidence/conversation-deployment-20261014.json'),'utf8'));
  expect(test.info().project.use.baseURL).toBe(deployed.url);
  await page.goto('/');
  await page.locator('.history-item').filter({hasText:/^你能干什么\s*$/}).click();
  const latest=page.locator('.message.assistant').last();
  await expect(latest).toContainText('FOREST 7319');
  await expect(latest).toBeInViewport();
  const pane=page.locator('.conversation-content');
  await pane.evaluate(el=>{el.scrollTop=0;});
  await expect.poll(()=>pane.evaluate(el=>el.scrollTop)).toBe(0);
  await page.locator('.composer textarea').fill('向上阅读时的草稿，不发送');
  await expect.poll(()=>pane.evaluate(el=>el.scrollTop)).toBe(0);
  await pane.evaluate(el=>{el.scrollTop=el.scrollHeight;});
  await page.screenshot({path:resolve('../workspace/evidence/conversation-deployed-latest-20261014.png'),fullPage:true});
});
