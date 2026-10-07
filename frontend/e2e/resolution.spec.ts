import {test,expect} from '@playwright/test';
import {writeFileSync,readFileSync,mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const root=fileURLToPath(new URL('../../',import.meta.url));
const evidence=(name:string)=>resolve(root,'workspace/evidence',name);
const trials=new Map<string,{taskId:string;source:string;hash:string}>();
const sha=(bytes:Buffer)=>createHash('sha256').update(bytes).digest('hex');
async function snapshot(page:any,taskId:string){
  const state=await(await page.request.get(`/tasks/${taskId}`)).json();
  const approvals=await(await page.request.get(`/tasks/${taskId}/approvals`)).json();
  const artifacts=await(await page.request.get(`/tasks/${taskId}/artifacts`)).json();
  const events:any[]=[];let cursor=0;
  for(let n=0;n<100;n++){
    const batch=await(await page.request.get(`/tasks/${taskId}/events?format=json&cursor=${cursor}`)).json();
    if(!batch.length)break;events.push(...batch);cursor=batch.at(-1).sequence;
  }
  const jobs:any[]=[];
  for(const id of new Set(events.filter(e=>e.type==='job.queued').map(e=>e.payload.job_id))){
    jobs.push(await(await page.request.get(`/tasks/${taskId}/jobs/${id}`)).json());
  }
  return {state,approvals,artifacts,events,jobs};
}
test.afterEach(async({page},info)=>{
  const trial=trials.get(info.title);if(!trial||info.status==='passed'||info.status==='skipped')return;
  writeFileSync(evidence(`ui-resolution-failure-${trial.taskId}.json`),JSON.stringify({task_id:trial.taskId,...await snapshot(page,trial.taskId),
    errors:info.errors,source_unchanged:sha(readFileSync(trial.source))===trial.hash,all_passed:false,general_stage_acceptance:'incomplete'},null,2));
});
for(const model of ['small','large'])test(`${model} resolves downloads installs and reuses dependencies through UI`,async({page})=>{
  test.skip(process.env.RUN_RESOLUTION_UI!=='1'||!!process.env.FORESTRY_RECHECK_RESOLUTION_TASK,'Explicit new real task; rechecks use the existing task only.');
  test.setTimeout(480000);
  const directory=resolve(root,'workspace/resolution-ui-fixtures',crypto.randomUUID());mkdirSync(directory,{recursive:true});
  const source=resolve(directory,`resolution-heights-${model}.csv`);writeFileSync(source,'height_m\n8.25\n12.50\n16.75\n');
  const hash=sha(readFileSync(source)),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');expect((await page.request.get('/session')).ok()).toBe(true);
  const grant=await page.request.post('/resources/register',{headers:{origin:new URL(page.url()).origin},
    data:{request_id:crypto.randomUUID(),path:source,classification:'private',read_only:true}});
  expect(grant.ok(),await grant.text()).toBe(true);const ref=(await grant.json()).resource_id;await page.reload();
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();
  await page.locator('.settings-modal select').selectOption(model);
  await expect.poll(async()=>(await(await page.request.get('/settings')).json()).default_model).toBe(model);
  await page.locator('.settings-modal .modal-heading button').click();
  await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await page.getByRole('combobox',{name:'工作方式',exact:true}).selectOption('H1');
  await page.getByRole('combobox',{name:'任务工具范围',exact:true}).selectOption('custom');
  for(const label of ['获取公开依赖','准备隔离依赖','执行受限代码','观察作业','写入产物','读回产物'])await page.getByLabel(label,{exact:true}).check();
  await page.locator('.source-chips button').filter({hasText:`resolution-heights-${model}.csv`}).last().click();
  await page.locator('.composer textarea').fill(`软件集成任务：仅用resolve_dependencies(requirements=["numpy==2.2.6","python-dateutil==2.9.0.post0"])解析完整依赖，不手写传递依赖wheel文件名。先提交无输入目标探测，wait_job成功后用execution-target.json的真实artifact_id再次解析；遇审批停止等待用户，批准后传匹配approval_id。解析成功用返回wheels和resolution_lock_artifact_id调用acquire_dependencies(source="tuna")，再等待精确下载审批。真实下载后用wheel_artifact_ids准备一次环境并wait_job；成功后复用同一prepared_environment.artifact_id执行两次，不重新安装。CSV资源ID=${ref}，列height_m。每次在容器读取CSV，用numpy求数量、总和、均值，用dateutil解析2026-10-06；保存resolution-result-1.json和resolution-result-2.json，字段tree_count,total_height_m,mean_height_m,parsed_date,numpy_version,dateutil_version,six_version（类型为数值/字符串）。wait_job成功后实际读回两个JSON，再write_file中文resolution-report.md，写实际数值、日期、三包版本、源资源ID、目标探测/安装/两次计算的真实job_id、复用环境ID、两类审批、断网安装/源只读与软件夹具不证明科学精度的局限，实际读回报告后final_result引用两个JSON和报告。只访问授权文件、公开依赖元数据/字节，不联网搜索、不宿主安装、不委派、不重复已成功工作。`);
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/tasks');
  await page.locator('.send-button').click();const response=await created;expect(response.status()).toBe(202);
  const taskId=(await response.json()).task_id;trials.set(test.info().title,{taskId,source,hash});
  test.info().annotations.push({type:'resolution_task_id',description:taskId});
  for(const operation of ['resolve','download']){
    let pending:any;
    await expect.poll(async()=>{
      const grants=await(await page.request.get(`/tasks/${taskId}/approvals`)).json();
      pending=grants.find((g:any)=>g.status==='pending'&&(operation==='resolve'?g.request.operation==='resolve':!g.request.operation));
      const state=await(await page.request.get(`/tasks/${taskId}`)).json();
      return pending?'pending':['paused','failed'].includes(state.status)?state.status:'working';
    },{timeout:180000,intervals:[1000,2000]}).toMatch(/^(pending|paused|failed)$/);
    expect(pending,'A terminal failed/paused task cannot supply the required approval.').toBeTruthy();
    expect(pending.request.package_execution).toBe(false);
    const value=JSON.parse(pending.request.value);
    if(operation==='resolve')expect(value.requirements).toEqual(['numpy==2.2.6','python-dateutil==2.9.0.post0']);
    else {expect(value.source).toBe('tuna');expect(value.wheels.map((w:any)=>w.name).sort()).toEqual(['numpy','python-dateutil','six']);}
    const panel=page.getByLabel('网络请求审批');
    if(operation==='resolve'){await expect(panel).toContainText('公开依赖元数据解析');await expect(panel).toContainText('包含传递依赖');}
    await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    await page.screenshot({path:evidence(`workbench-resolution-${model}-${operation}-${taskId}-mobile.png`),fullPage:true});
    await panel.locator('.network-approval').filter({hasText:pending.request_sha256}).getByRole('button',{name:'批准这项请求',exact:true}).click();
    await expect.poll(async()=>(await(await page.request.get(`/tasks/${taskId}/approvals`)).json()).find((g:any)=>g.approval_id===pending.approval_id).status,
      {timeout:60000,intervals:[1000,2000]}).toBe('consumed');
  }
  await expect.poll(async()=>(await(await page.request.get(`/tasks/${taskId}`)).json()).status,
    {timeout:200000,intervals:[1000,2000]}).toMatch(/^(waiting_user|paused|failed)$/);
  await validateOutcome(page,await snapshot(page,taskId),taskId,model,ref,source,hash,errors);
});

async function validateOutcome(page:any,observed:any,taskId:string,model:string,ref:string,source:string,hash:string,errors:string[],suffix=''){
  const path=evidence(`ui-resolution-${model}-${taskId}${suffix}.json`);
  const record={model,task_id:taskId,...observed,all_passed:false,general_stage_acceptance:'incomplete',scientific_accuracy:'not_assessed'};
  writeFileSync(path,JSON.stringify(record,null,2));expect(observed.state.status).toBe('waiting_user');
  expect(observed.approvals).toHaveLength(2);expect(observed.approvals.every((g:any)=>g.status==='consumed')).toBe(true);
  expect(observed.jobs).toHaveLength(4);expect(observed.jobs.every(j=>j.status==='succeeded')).toBe(true);
  const installation=observed.jobs.filter(j=>j.kind==='environment.prepare');expect(installation).toHaveLength(1);
  const target=observed.artifacts.find((a:any)=>a.name==='execution-target.json');expect(target).toBeTruthy();
  const probe=observed.jobs.find((j:any)=>j.action_id===target.producer_action_id);expect(probe).toBeTruthy();
  const probeCode=readFileSync(resolve(root,'src/forestry_agent/resolution.py'),'utf8').replace(/\r\n/g,'\n').match(/PROBE_CODE = '''([\s\S]*?)'''/)![1];
  expect(probe.code_sha256).toBe(sha(Buffer.from(probeCode)));
  const actualTarget=await(await page.request.get(target.preview_url)).json();expect(actualTarget.sys_platform).toBe('linux');expect(actualTarget.python_full_version).toMatch(/^3\.12\./);
  const computations=observed.jobs.filter(j=>j.environment);expect(computations).toHaveLength(2);
  expect(computations.every(j=>j.environment.artifact_id===computations[0].environment.artifact_id&&j.image_id===probe.image_id)).toBe(true);
  for(const name of ['resolution-result-1.json','resolution-result-2.json','resolution-report.md']){
    const product=observed.artifacts.find((a:any)=>a.name===name);expect(product).toBeTruthy();
    expect(observed.events.some(e=>e.type==='action.observed'&&e.payload.tool==='artifact.read'&&e.payload.result.kind==='completed'&&e.payload.result.artifact_id===product.artifact_id)).toBe(true);
    if(name.endsWith('.json'))expect(await(await page.request.get(product.preview_url)).json()).toEqual({tree_count:3,total_height_m:37.5,mean_height_m:12.5,
      parsed_date:'2026-10-06',numpy_version:'2.2.6',dateutil_version:'2.9.0.post0',six_version:'1.17.0'});
    else {const text=await(await page.request.get(product.preview_url)).text();expect(text).toMatch(/[\u4e00-\u9fff]/);
      for(const id of [...observed.jobs.map((j:any)=>j.job_id),ref,computations[0].environment.artifact_id,'37.5','12.5','1.17.0','2.9.0.post0','2.2.6'])expect(text).toContain(id);}
  }
  expect(observed.events.some(e=>e.type==='action.started'&&['web.search','web.read','role.delegate'].includes(e.payload.tool))).toBe(false);
  expect(sha(readFileSync(source))).toBe(hash);expect(errors).toEqual([]);
  writeFileSync(path,JSON.stringify({...record,all_passed:true,source_unchanged:true},null,2));
}

test('recheck an existing resolution workflow without a new model or job',async({page})=>{
  const taskId=process.env.FORESTRY_RECHECK_RESOLUTION_TASK;
  test.skip(process.env.RUN_RESOLUTION_UI!=='1'||!taskId,'Explicit existing real task.');
  await page.goto('/');expect((await page.request.get('/session')).ok()).toBe(true);
  const observed=await snapshot(page,taskId!);
  const resources=await(await page.request.get('/resources')).json();
  expect(observed.state.input_refs).toHaveLength(1);const ref=observed.state.input_refs[0];
  const source=resources.find((r:any)=>r.resource_id===ref).locator;
  const expectedHash=sha(Buffer.from('height_m\n8.25\n12.50\n16.75\n'));
  await validateOutcome(page,observed,taskId!,observed.state.model_profile,ref,source,expectedHash,[],'-recheck');
  const after=await snapshot(page,taskId!);
  expect(after.state.model_requests).toBe(observed.state.model_requests);
  expect(after.jobs.map(j=>j.job_id)).toEqual(observed.jobs.map(j=>j.job_id));
});
