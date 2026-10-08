import {test,expect} from '@playwright/test';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const root=fileURLToPath(new URL('../../',import.meta.url));
const evidence=(name:string)=>resolve(root,'workspace/evidence',name);
const sha=(bytes:Buffer)=>createHash('sha256').update(bytes).digest('hex');
const trials=new Map<string,{taskId:string;source:string;hash:string;model:string}>();
const roots=['docopt==0.6.2'];
const equalSet=(a:any[],b:any[])=>JSON.stringify([...a].sort())===JSON.stringify([...b].sort());

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
  writeFileSync(evidence(`ui-public-source-failure-${trial.taskId}.json`),JSON.stringify({task_id:trial.taskId,model:trial.model,
    ...await snapshot(page,trial.taskId),errors:info.errors,source_unchanged:sha(readFileSync(trial.source))===trial.hash,
    all_passed:false,general_stage_acceptance:'incomplete',scientific_accuracy:'not_assessed'},null,2));
  await page.screenshot({path:evidence(`workbench-public-source-failure-${trial.taskId}.png`),fullPage:true});
});

async function approveScopedRequests(page:any,taskId:string,model:string){
  const decisions:any[]=[];
  for(let n=0;n<15;n++){
    let pending:any;
    await expect.poll(async()=>{
      const grants=await(await page.request.get(`/tasks/${taskId}/approvals`)).json();
      pending=grants.find((g:any)=>g.status==='pending');
      const state=await(await page.request.get(`/tasks/${taskId}`)).json();
      return pending?'pending':['paused','failed','cancelled'].includes(state.status)?state.status:
        state.status==='waiting_user'&&!grants.some((g:any)=>g.status==='approved')?'waiting_user':'working';
    },{timeout:600000,intervals:[1000,2000]}).toMatch(/^(pending|waiting_user|paused|failed|cancelled)$/);
    if(!pending)break;
    expect(pending.request.kind).toBe('environment.acquire');expect(pending.request.package_execution).toBe(false);
    const observed=await snapshot(page,taskId);
    const results=observed.events.filter(e=>e.type==='action.observed'&&e.payload.result.kind==='completed').map(e=>e.payload.result);
    const inspections=results.map(r=>r.build_requirements).filter(Boolean);
    const value=JSON.parse(pending.request.value);let phase:string;
    if(pending.request.operation==='resolve'){
      expect(pending.request.metadata_only).toBe(true);
      if(equalSet(value.requirements,roots)){
        phase=value.built_wheels?.length?'runtime-metadata':'source-metadata';
        if(value.built_wheels?.length)expect(value.built_wheels.every((w:any)=>observed.artifacts.some((a:any)=>a.artifact_id===w.artifact_id&&a.sha256===w.sha256))).toBe(true);
      }else{
        const matching=inspections.find(r=>value.requirements.length&&value.requirements.every((q:string)=>[...r.requirements,...r.frontend_requirements].includes(q)));
        expect(matching,'Build roots must come from an actual source inspection, not a guessed tool list.').toBeTruthy();
        phase=`build-metadata-${inspections.at(-1)?.inspection}-${JSON.stringify([...value.requirements].sort())}`;
      }
    }else{
      let lock:any;
      if(pending.request.resolution_lock_artifact_id){
        const artifact=observed.artifacts.find((a:any)=>a.artifact_id===pending.request.resolution_lock_artifact_id);
        expect(artifact).toBeTruthy();expect(artifact.sha256).toBe(pending.request.resolution_lock_sha256);
        lock=await(await page.request.get(artifact.preview_url)).json();
      }
      if(value.source_packages?.length){
        phase='source-download';
        const candidate=results.find(r=>equalSet((r.source_candidates||[]).map((p:any)=>JSON.stringify(p)),value.source_packages.map((p:any)=>JSON.stringify(p))));
        expect(candidate,'The exact filename must originate in the actual resolver candidate.').toBeTruthy();
        expect(candidate.source_candidate_sha256['docopt-0.6.2.tar.gz']).toBe('49b3a825280bd66b3aa83585ef59c4a8c82f2c8a522dbe754a8bc8d08c85c491');
        expect(value.wheels).toEqual([]);expect(value.source_packages).toEqual([{name:'docopt',version:'0.6.2',filename:'docopt-0.6.2.tar.gz'}]);
        if(lock){expect(lock.kind).toBe('python_source_candidate');expect(lock.resolution_complete).toBe(false);
          expect(lock.selected[0].sha256).toBe(candidate.source_candidate_sha256['docopt-0.6.2.tar.gz']);}
      }else{
        phase=`wheel-download-${inspections.at(-1)?.inspection}-${JSON.stringify(value.wheels.map((w:any)=>w.name).sort())}`;
        if(lock){
          expect(lock.kind).toBe('python_wheel_resolution');
          const selected=lock.selected.filter((w:any)=>!w.artifact_id).map((w:any)=>JSON.stringify({name:w.name,version:w.version,filename:w.filename}));
          expect(equalSet(value.wheels.map((w:any)=>JSON.stringify(w)),selected)).toBe(true);
        }else expect(value.wheels.length&&value.wheels.every((w:any)=>results.some(r=>(r.wheels||[]).some((p:any)=>equalSet([JSON.stringify(p)],[JSON.stringify(w)]))))).toBe(true);
        expect(value.wheels.every((w:any)=>['setuptools','wheel','pyproject-hooks','packaging'].includes(w.name))).toBe(true);
      }
      expect(['pypi','tuna']).toContain(value.source);
    }
    const previous=decisions.filter(d=>d.phase===phase);expect(previous.length,'At most two retries per observed phase.').toBeLessThan(3);
    if(previous.length){
      const prior=previous.at(-1);
      const grant=observed.approvals.find((g:any)=>g.approval_id===prior.approval_id);
      const outcome=observed.events.find(e=>e.type==='action.observed'&&e.payload.result.action_id===grant.consumed_action_id)?.payload.result;
      expect(outcome?.kind,'Repeat approval requires an actual failed operation.').toBe('failed');
      expect(pending.request_sha256).toBe(prior.request_sha256);
    }
    decisions.push({phase,approval_id:pending.approval_id,request_sha256:pending.request_sha256});
    writeFileSync(evidence(`ui-public-source-decisions-${taskId}.json`),JSON.stringify(decisions,null,2));
    const panel=page.getByLabel('网络请求审批');
    await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    await page.screenshot({path:evidence(`workbench-public-source-${model}-${taskId}-approval-${n+1}.png`),fullPage:true});
    await panel.locator('.network-approval').filter({hasText:pending.request_sha256}).getByRole('button',{name:'批准这项请求',exact:true}).click();
    let consumedState:string;
    await expect.poll(async()=>{
      const grant=(await(await page.request.get(`/tasks/${taskId}/approvals`)).json()).find((g:any)=>g.approval_id===pending.approval_id);
      const state=await(await page.request.get(`/tasks/${taskId}`)).json();
      consumedState=grant.status==='consumed'?'consumed':['paused','failed','cancelled'].includes(state.status)?state.status:'working';
      return consumedState;
    },{timeout:320000,intervals:[1000,2000]}).toMatch(/^(consumed|paused|failed|cancelled)$/);
    expect(consumedState!,'Approval continuation must consume its exact grant before proceeding.').toBe('consumed');
    await page.setViewportSize({width:1440,height:960});
  }
  return decisions;
}

for(const model of ['small','large'])test(`${model} discovers public source build dependencies and delivers two actual calls through UI`,async({page})=>{
  test.skip(process.env.RUN_PUBLIC_SOURCE_UI!=='1','Explicit approved public package and real model trial.');
  test.setTimeout(900000);
  const directory=resolve(root,'workspace/public-source-ui-fixtures',crypto.randomUUID());mkdirSync(directory,{recursive:true});
  const source=resolve(directory,`docopt-call-notes-${model}.md`);
  writeFileSync(source,'# 调用数据\n\n软件包：docopt==0.6.2。\n函数：docopt.docopt(doc, argv=...)。\n\n使用说明文本：Usage: example [--flag] <value>\n\n第一次 argv=["--flag","alpha"]；第二次 argv=["beta"]。\n输出 JSON 包含 parsed（函数真实返回的对象）和 version（importlib.metadata 实际读取的版本字符串）。\n');
  const hash=sha(readFileSync(source)),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');expect((await page.request.get('/session')).ok()).toBe(true);
  await expect.poll(async()=>{
    const tasks=await(await page.request.get('/tasks')).json();
    return tasks.every((t:any)=>!['created','active','waiting_job','cancelling','publishing'].includes(t.status));
  },{timeout:320000,intervals:[1000,2000]}).toBe(true);
  const grant=await page.request.post('/resources/register',{headers:{origin:new URL(page.url()).origin},
    data:{request_id:crypto.randomUUID(),path:source,classification:'private',read_only:true}});
  expect(grant.ok(),await grant.text()).toBe(true);const ref=(await grant.json()).resource_id;await page.reload();
  await page.getByRole('button',{name:'模型与环境设置',exact:true}).click();await page.locator('.settings-modal select').selectOption(model);
  await expect.poll(async()=>(await(await page.request.get('/settings')).json()).default_model).toBe(model);
  await page.locator('.settings-modal .modal-heading button').click();await page.getByRole('button',{name:'新建任务',exact:true}).click();
  await page.getByRole('combobox',{name:'工作方式',exact:true}).selectOption('H1');
  await page.getByRole('combobox',{name:'任务工具范围',exact:true}).selectOption('custom');
  for(const label of ['读取源文件','获取公开依赖','准备隔离依赖','执行受限代码','观察作业','写入产物','读回产物','查看动作证据','跟踪任务计划'])await page.getByLabel(label,{exact:true}).check();
  await page.locator('.source-chips button').filter({hasText:`docopt-call-notes-${model}.md`}).last().click();
  await page.locator('.composer textarea').fill(`读取所选调用说明（资源ID=${ref}），完成docopt==0.6.2的软件集成。先由依赖解析器选择发行候选，不猜源码文件名；若需要源码，取得后真实检查静态和动态构建依赖，补齐已核验wheel，在断网作业构建一次，依据生成wheel的实际元数据补齐运行依赖并独立安装一次。复用该环境按说明的两组不同argv各调用一次docopt.docopt，计算作业分别保存public-source-result-1.json和public-source-result-2.json，字段parsed、version，实际读回两文件。写中文source-discovery-report.md，记录实际版本、两次结果、源码和环境的实际产物引用及执行边界/局限，再读回报告并引用交付文件。公开依赖的元数据/精确下载审批由用户逐项处理；私密调用说明保持本地。不联网搜索、不宿主安装、不委派，复用已成功的下载/构建产物，不猜测运行结果。`);
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/tasks');
  await page.locator('.send-button').click();const response=await created;expect(response.status()).toBe(202);
  const taskId=(await response.json()).task_id;trials.set(test.info().title,{taskId,source,hash,model});
  test.info().annotations.push({type:'public_source_task_id',description:taskId});
  const decisions=await approveScopedRequests(page,taskId,model);
  const observed=await snapshot(page,taskId);
  const path=evidence(`ui-public-source-${model}-${taskId}.json`);
  const record={model,task_id:taskId,source,source_sha256:hash,...observed,decisions,all_passed:false,
    scientific_accuracy:'not_assessed',general_stage_acceptance:'incomplete'};
  writeFileSync(path,JSON.stringify(record,null,2));
  expect(observed.state.status).toBe('waiting_user');expect(observed.state.input_refs).toEqual([ref]);
  expect(observed.approvals.every((g:any)=>g.status==='consumed')).toBe(true);
  expect(observed.jobs.every(j=>['succeeded','failed'].includes(j.status))).toBe(true);
  const successful=observed.jobs.filter(j=>j.status==='succeeded');
  const inspections=successful.filter(j=>j.source_inspection);expect(new Set(inspections.map(j=>j.source_inspection))).toEqual(new Set(['static','dynamic']));
  const builds=successful.filter(j=>j.source_build_only);expect(builds).toHaveLength(1);
  const installations=successful.filter(j=>j.kind==='environment.prepare'&&!j.source_inspection&&!j.source_build_only);expect(installations).toHaveLength(1);
  const results=observed.events.filter(e=>e.type==='action.observed'&&e.payload.result.kind==='completed').map(e=>e.payload.result);
  expect(results.some(r=>r.source_candidates?.length===1&&r.resolution_complete===false)).toBe(true);
  expect(results.some(r=>r.built_wheel_artifact_ids?.length&&r.resolution_lock_artifact_id)).toBe(true);
  const sourceArtifact=observed.artifacts.find((a:any)=>a.name==='docopt-0.6.2.tar.gz');expect(sourceArtifact).toBeTruthy();
  const archive=observed.artifacts.find((a:any)=>a.name==='environment.zip');expect(archive).toBeTruthy();
  const manifest=await(await page.request.get(observed.artifacts.find((a:any)=>a.name==='environment-lock.json').preview_url)).json();
  expect(manifest.installed).toEqual([{name:'docopt',version:'0.6.2'}]);
  const products=['public-source-result-1.json','public-source-result-2.json','source-discovery-report.md'].map(name=>observed.artifacts.find((a:any)=>a.name===name));
  const expected=[{parsed:{'--flag':true,'<value>':'alpha'},version:'0.6.2'},{parsed:{'--flag':false,'<value>':'beta'},version:'0.6.2'}];
  for(const [index,product] of products.slice(0,2).entries()){
    expect(product).toBeTruthy();expect(await(await page.request.get(product.preview_url)).json()).toEqual(expected[index]);
    expect(successful.some(j=>j.kind==='code.execute'&&j.environment?.artifact_id===archive.artifact_id&&j.action_id===product.producer_action_id&&j.artifact_ids.includes(product.artifact_id))).toBe(true);
  }
  for(const product of products){expect(product).toBeTruthy();expect(results.some(r=>r.artifact_id===product.artifact_id&&typeof r.text==='string')).toBe(true);}
  const report=await(await page.request.get(products[2].preview_url)).text();expect(report).toMatch(/[\u4e00-\u9fff]/);
  for(const value of ['0.6.2','alpha','beta',sourceArtifact.artifact_id,archive.artifact_id])expect(report).toContain(value);
  const known=new Set([taskId,ref,...observed.artifacts.map((a:any)=>a.artifact_id),...observed.jobs.map(j=>j.job_id),
    ...observed.events.filter(e=>e.type==='action.started').map(e=>e.payload.action_id)]);
  expect((report.match(/\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/gi)||[]).filter(id=>!known.has(id.toLowerCase()))).toEqual([]);
  expect(observed.events.some(e=>e.type==='action.started'&&['web.search','web.read','role.delegate'].includes(e.payload.tool))).toBe(false);
  expect(sha(readFileSync(source))).toBe(hash);expect(errors).toEqual([]);
  await page.getByRole('button',{name:'source-discovery-report.md',exact:true}).last().click();
  await expect(page.locator('.preview-modal pre')).toContainText('alpha');
  await page.screenshot({path:evidence(`workbench-public-source-${model}-${taskId}.png`),fullPage:true});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:evidence(`workbench-public-source-${model}-${taskId}-mobile.png`),fullPage:true});
  await page.locator('.preview-modal .modal-heading button').click();await page.reload();await expect(page.locator('.task-status')).toHaveText('待确认');
  writeFileSync(path,JSON.stringify({...record,all_passed:true,source_unchanged:true},null,2));
});
