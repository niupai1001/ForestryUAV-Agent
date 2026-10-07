# 原生计划与工作台

多步骤任务在 H1/H2 中可使用“跟踪任务计划”（task.plan）。自定义工具范围须明确勾选；“文件与报告”预设未包含此能力。“通用能力”仍受冻结的角色和应用上限约束。H0没有计划工具；历史任务不因升级扩大冻结权限。

模型通过框架原生 write_plan 保存完整步骤，通过 read_plan 找回步骤 ID。最多16步、8192 UTF-8字节，每步正文1–500字符、ID1–64字符、active_form最多120字符，最多一步进行中。本次不开放子任务/依赖图或额外计划调度器。计划工具沿用现有动作、权限和预算；只改计划或反复读计划不构成执行进展。

工作台右侧“计划”显示当前规范最新根回合的步骤和自报状态。计划和真实作业、产物、验收分别检查：助手标记完成仍是 model_proposed，acceptance 为 not_assessed。计划不能授予权限、改方法或将任务验收通过。后续回合或规范修订不会继承另一命名空间的计划；子角色计划独立保存，不在根计划视图中合并。

认证只读接口 `GET /tasks/{task_id}/plan` 返回 task_id、spec_revision、turn_id、authority、acceptance 和原生 items；不存在任务返回404。模型正文和文件里的“完成”文字不参与验收。UI合并历史事件触发的详情刷新，避免并发刷新堆积；当前任务只展示其授权输入，名称相同的历史登记项不会合并资源身份或权限。

计划使用框架 SqlitePlanStore 和同一应用数据库中的 native_plan_items。应用仅增加范围与写入上限检查；没有第二套计划执行循环、消息历史或作业存储。控制和计划写入在当前单服务的应用锁下协调；未证明多个服务进程同时写同一库的并发控制，应按当前单服务部署使用。

当前框架的 Planning 在 wrap_model_request 注入提醒，而压缩器在后续 before-model 阶段从持久历史重建消息，可能移除提醒。兼容适配调用原生提醒注入方法，将它移到压缩之后、输入预算与私有快照之前。原生工具、格式、缓存锚点、读写和持久化继续由框架负责；提醒保持临时内容，不累积进历史。

协议夹具8项及完整回归177项通过；桌面/390px手机的真实已存计划、授权来源、刷新和溢出检查通过。修复后的两模型43次实际请求分别核对当前计划、输入摘要与用量，提醒均存在并对应先前确认的计划动作。这些证明集成和边界，不证明模型会正确执行或更新计划：最新依赖任务两模型都暂停，小模型误用环境且上下文超限，大模型完成文件后重复读取触发无进展。原始失败及上下文审计见 STATUS，完整通用阶段仍未验收。

English: Native scoped planning reuses the existing framework, database, action journal and budgets. Its UI shows model-proposed progress only. The compatibility adapter retains the native ephemeral reminder after compaction without accumulating history. Protocol, regression and request audits pass; latest real dependency workflow quality fails for both models and remains pending.
