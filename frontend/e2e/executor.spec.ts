import { test, expect } from '@playwright/test';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root=fileURLToPath(new URL('../../',import.meta.url));
const evidencePath=(name:string)=>resolve(root,'workspace/evidence',name);
const originals=new Map<string,{paths:string[],bytes:Buffer[]}>();

async function snapshot(page:any,taskId:string){
  const state=await (await page.request.get(`/tasks/${taskId}`)).json();
  const events:any[]=[];let cursor=0;
  for(let n=0;n<100;n++){
    const batch=await (await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;events.push(...batch);cursor=batch.at(-1).sequence;
  }
  const ids=[...new Set(events.filter(e=>e.type==='job.queued').map(e=>e.payload.job_id))];
  const jobs:any[]=[];
  for(const id of ids){
    const job=await (await page.request.get(`/tasks/${taskId}/jobs/${id}`)).json();
    const logs=await (await page.request.get(`/tasks/${taskId}/jobs/${id}/logs`)).json();
    jobs.push({job,logs});
  }
  return {state,events,jobs,artifacts:await (await page.request.get(`/tasks/${taskId}/artifacts`)).json()};
}

for(const model of ['small','large'])test(`${model} debugs a real failing container job through the workbench`,async({page})=>{
  test.skip(process.env.RUN_EXECUTOR_UI!=='1','Explicit real model and real isolated executor trial.');
  test.setTimeout(300000);
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const directory=resolve(root,'workspace/executor-ui-fixtures',crypto.randomUUID());mkdirSync(directory,{recursive:true});
  const paths=[resolve(directory,'heights.csv'),resolve(directory,'broken.py')];
  const contents=['height_m\n8.25\n12.50\n16.75\n',
    "import csv, json\nfrom pathlib import Path\nINPUT_PATH='/inputs/REPLACE_ME'\nrows=list(csv.DictReader(Path(INPUT_PATH).open()))\nheights=[float(row['height_m']) for row in rows]\nresult={'tree_count':len(height_values),'total_height_m':sum(height_values),'mean_height_m':sum(height_values)/len(height_values)}\nPath('/output/code-result.json').write_text(json.dumps(result),encoding='utf-8')\n"];
  paths.forEach((p,i)=>writeFileSync(p,contents[i]));
  await page.goto('/');expect((await page.request.get('/session')).ok()).toBe(true);
  const refs:string[]=[];
  for(const path of paths){
    const reply=await page.request.post('/resources/register',{headers:{origin:new URL(page.url()).origin},
      data:{request_id:crypto.randomUUID(),path,classification:'private',read_only:true}});
    expect(reply.ok(),await reply.text()).toBe(true);refs.push((await reply.json()).resource_id);
  }
  await page.reload();
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await page.locator('.settings-modal select').selectOption(model);
  await expect.poll(async()=>(await (await page.request.get('/settings')).json()).default_model).toBe(model);
  await page.locator('.settings-modal .modal-heading button').click();
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await page.getByRole('combobox',{name:'工作方式',exact:true}).selectOption('H1');
  await page.getByRole('combobox',{name:'任务工具范围',exact:true}).selectOption('custom');
  for(const name of ['查看任务输入','读取源文件','写入产物','检查产物','读回产物','查看动作证据','执行受限代码','观察作业'])await page.getByLabel(name,{exact:true}).check();
  await page.locator('.source-chips button').filter({hasText:'heights.csv'}).last().click();
  await page.locator('.source-chips button').filter({hasText:'broken.py'}).last().click();
  await page.locator('.composer textarea').fill(`完成真实代码调试任务。只读读取两个已选文件，CSV资源ID=${refs[0]}，脚本资源ID=${refs[1]}。第一步只把原脚本INPUT_PATH改成/inputs/${refs[0]}，保留height_values变量错误，用code_execute实际运行它，用wait_job查看真实错误日志。然后根据真实NameError修复脚本，再实际code_execute运行并wait_job等待；不要仅心算、不要捕获错误伪造成功。输出code-result.json必须来自成功容器，数字字段tree_count、total_height_m、mean_height_m；另write_file保存修复后的code-fixed.py与中文code-report.md，说明真实失败、修复、输入来源和软件夹具不代表科学精度。实际read_artifact读回结果和报告，final_result引用三个真实产物ID，等待我验收。禁止联网、依赖安装、角色委派或修改源文件。`);
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/tasks');
  await page.locator('.send-button').click();
  const reply=await created;expect(reply.status()).toBe(202);const taskId=(await reply.json()).task_id;
  test.info().annotations.push({type:'executor_task_id',description:taskId});
  originals.set(taskId,{paths,bytes:contents.map(c=>Buffer.from(c))});
  await expect.poll(async()=>(await (await page.request.get(`/tasks/${taskId}`)).json()).status,
    {timeout:230000,intervals:[1000,2000]}).toMatch(/^(waiting_user|paused|failed|cancelled|completed)$/);
  const observed=await snapshot(page,taskId);
  const output=evidencePath(`ui-executor-${model}-${taskId}.json`);
  writeFileSync(output,JSON.stringify({model,task_id:taskId,...observed,all_passed:false,
    scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete'},null,2));
  expect(observed.state.status).toBe('waiting_user');expect(observed.state.model_profile).toBe(model);
  const failed=observed.jobs.find(({job,logs})=>job.status==='failed'&&job.exit_code!==0&&logs.stderr?.includes('NameError'));
  const passed=observed.jobs.find(({job})=>job.status==='succeeded');
  expect(failed).toBeTruthy();expect(passed).toBeTruthy();
  expect(observed.events.some(e=>e.type==='action.observed'&&e.payload.tool==='job.observe'&&
    e.payload.result.logs?.stderr?.includes('NameError'))).toBe(true);
  const result=observed.artifacts.findLast((a:any)=>a.name==='code-result.json');
  const report=observed.artifacts.findLast((a:any)=>a.name==='code-report.md');
  const fixed=observed.artifacts.findLast((a:any)=>a.name==='code-fixed.py');
  expect(result).toBeTruthy();expect(report).toBeTruthy();expect(fixed).toBeTruthy();
  expect(passed.job.artifact_ids).toContain(result.artifact_id);
  expect(await (await page.request.get(result.preview_url)).json()).toEqual({tree_count:3,total_height_m:37.5,mean_height_m:12.5});
  const reportText=await (await page.request.get(report.preview_url)).text();expect(reportText).toMatch(/[\u4e00-\u9fff]/);expect(reportText).toContain('NameError');
  for(const artifact of [result,report])expect(observed.events.some(e=>e.type==='action.started'&&e.payload.tool==='artifact.read'&&e.payload.arguments.artifact_id===artifact.artifact_id)).toBe(true);
  expect(observed.events.some(e=>e.type==='action.started'&&['web.search','web.read','role.delegate','memory.read','environment.inspect'].includes(e.payload.tool))).toBe(false);
  paths.forEach((p,i)=>expect(readFileSync(p).equals(Buffer.from(contents[i]))).toBe(true));
  await page.getByRole('button',{name:'code-report.md',exact:true}).last().click();
  await expect(page.locator('.preview-modal pre')).toContainText('NameError');
  await page.screenshot({path:evidencePath(`workbench-executor-${model}-${taskId}.png`),fullPage:true});
  await page.locator('.preview-modal .modal-heading button').click();await page.reload();
  await expect(page.locator('.task-status')).toHaveText('待确认');expect(errors).toEqual([]);
  writeFileSync(output,JSON.stringify({model,task_id:taskId,...observed,all_passed:true,source_unchanged:true,
    scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete'},null,2));
});

test.afterEach(async({page},info)=>{
  if(info.status===info.expectedStatus)return;
  const taskId=info.annotations.find(a=>a.type==='executor_task_id')?.description;if(!taskId)return;
  const source=originals.get(taskId);
  writeFileSync(evidencePath(`ui-executor-failure-${taskId}.json`),JSON.stringify({task_id:taskId,...await snapshot(page,taskId),
    source_unchanged:source?.paths.every((p,i)=>readFileSync(p).equals(source.bytes[i])),
    all_passed:false,error:info.error?.message,general_stage_acceptance:'incomplete',scientific_accuracy:'not_assessed'},null,2));
});
