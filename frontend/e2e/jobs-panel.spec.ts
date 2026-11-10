import { test, expect } from '@playwright/test';
import { resolve } from 'node:path';

test('job source, result, cancellation acknowledgement and logs survive refresh',async({page})=>{
  let cancelled=false,terminal=false;
  const job=()=>({job_id:'job-fixture',readable_name:'正射拼接',kind:'code.execute',
    status:terminal?'cancelled':'running',cancel_requested:cancelled,request_turn_id:'source-turn',
    created_at:'2026-10-26T03:00:00Z',artifact_ids:['report-fixture']});
  await page.route('**/tasks/task-fixture/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname.endsWith('/cancel')){
      expect(request.method()).toBe('POST');
      expect(request.postDataJSON().request_id).toMatch(/^[0-9a-f-]{36}$/);
      cancelled=true;
      await route.fulfill({json:job()});
    }else if(url.pathname.endsWith('/logs')){
      await route.fulfill({json:{text:'已保留已有成果，等待执行器退出确认',bounded:true}});
    }else await route.fulfill({json:{jobs:[job()],next:1,has_more:false}});
  });
  await page.goto('/ui/e2e/fixtures/jobs-panel.html');
  await expect(page.getByRole('heading',{name:'运行中 · 正射拼接'})).toBeVisible();
  await expect(page.getByText('拼接本次多光谱航片并保留结果',{exact:false})).toBeVisible();
  await expect(page.getByRole('link',{name:'航次报告.pdf'})).toHaveAttribute('href','/artifacts/report-fixture/content?download=true');
  await expect(page.getByRole('button',{name:'查看日志'})).not.toBeVisible();
  await page.getByRole('button',{name:'取消作业',exact:true}).click();
  await expect(page.getByRole('button',{name:'已请求取消，等待执行器确认'})).toBeDisabled();
  await expect(page.getByRole('heading',{name:'运行中 · 正射拼接'})).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button',{name:'已请求取消，等待执行器确认'})).toBeDisabled();
  await page.getByText('日志与执行详情',{exact:true}).click();
  await page.getByRole('button',{name:'查看日志'}).click();
  await expect(page.locator('.jobs-panel pre')).toContainText('等待执行器退出确认');
  terminal=true;
  await page.getByRole('button',{name:'刷新作业'}).click();
  await expect(page.getByRole('heading',{name:'已取消 · 正射拼接'})).toBeVisible();
  await expect(page.getByRole('button',{name:'已请求取消，等待执行器确认'})).toHaveCount(0);
  await page.screenshot({path:resolve(`../workspace/evidence/jobs-panel-${process.env.FORESTRY_JOBS_EVIDENCE||'20261026'}.png`),fullPage:true});
});

test('completion attention is visible and user can skip analysis without hiding results',async({page})=>{
  let state='pending';
  await page.route('**/tasks/task-fixture/**',async route=>{
    if(new URL(route.request().url()).pathname.endsWith('/delivery')){
      const action=route.request().postDataJSON().action;
      expect(['present','skip']).toContain(action);
      state=action==='present'?'presented':'skipped';
      await route.fulfill({json:{state,consumer_status:'paused'}});
      return;
    }
    await route.fulfill({json:{jobs:[{job_id:'job-attention',readable_name:'影像处理',kind:'code.execute',status:'succeeded',
      request_turn_id:'source-turn',artifact_ids:['report-fixture'],delivery:{state,consumer_status:'paused',
        failure:state==='needs_attention'?{code:'REPORT_FAILED',message:'后续报告未完成'}:null}}],next:1,has_more:false}});
  });
  await page.goto('/ui/e2e/fixtures/jobs-panel.html');
  await expect(page.getByRole('heading',{name:'执行完成 · 影像处理'})).toBeVisible();
  await page.getByRole('button',{name:'标记已查看'}).click();
  await expect(page.getByText('已查看回执',{exact:true})).toBeVisible();
  state='needs_attention';
  await page.getByRole('button',{name:'刷新作业'}).click();
  await expect(page.getByText('后续分析需要处理',{exact:true})).toBeVisible();
  await expect(page.getByText('后续分析未完成：后续报告未完成',{exact:false})).toBeVisible();
  await page.screenshot({path:resolve(`../workspace/evidence/delivery-attention-${process.env.FORESTRY_JOBS_EVIDENCE||'20261026'}.png`),fullPage:true});
  await page.getByRole('button',{name:'跳过后续分析'}).click();
  await page.reload();
  await expect(page.getByText('已跳过后续分析',{exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:'航次报告.pdf'})).toBeVisible();
  await expect(page.getByRole('button',{name:'跳过后续分析'})).toHaveCount(0);
});
