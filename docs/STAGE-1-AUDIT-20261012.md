# 阶段1证据收口与重复验证整改

此次只整理一次阶段契约验收和历史记录，没有新增产品流程、模型请求或真实计算作业。主服务和产品实现未变。后续不得把这份验收重新作为全量执行队列。

确定性验收首次454项中389项通过；63项因Windows测试临时路径过长失败，两项仍期待重复获取产生新产物，与现行已验证文件复用行为冲突。缩短独立临时路径，纠正这两条测试，并增加无新增HTTP请求、相同产物及原始生产者断言。仅复核原失败65项，全部通过。389项结果直接复用；454项不同案例均有通过证据，原失败XML保留，没有第二次全量回归或产品代码变更。

证据在`workspace/evidence/stage1-contracts-20261012-v1.xml`、`stage1-contracts-failed-only-scope-20261012-v1.json`、`stage1-contracts-failed-only-20261012-v2.xml`。测试通过只证明其声明的契约行为，不等于模型质量或整个阶段通过。

只读历史审计确认v1已有small/large × H0/H1/H2六个首轮记录，实际输入、冻结模型摘要、试次绑定及独立答案隔离保持；小模型权重与当前目标一致。原六项自动完整评分均失败，12个重复轮次未运行，不能把失败列为未覆盖，也不能抹去分母。v2–v5各自失败、未运行及v4独立纠错评分保留。

审计器最初受当前配置严格核验拒绝，之后又误处理未运行项的空评分；仅修复独立只读审计器，不修改产品验证器或旧清单。审计按原摘要、输入、试次及真实上下文核验历史记录，明确历史配置与实现不等同当前版本。最终记录为`workspace/evidence/stage1-pilot-retrospective-20261012-v1.json`。

勘误：此前“下一批补small/H0、small/H2覆盖”撤销；这些组合已经运行过，不能成为无变更新建实验的理由。正式当前版本的重复/配对比较留在阶段3。已有来源浏览、知识/显式记忆、角色冲突/恢复/撤权、作业控制及结果复用流程按各自范围冻结。

下一步只处理固定验收清单中尚缺的具体用户行为或证据裁决，必须明确哪一步不能完成及受影响实现。不得以“综合覆盖不足”“需要更多稳定性”无限追加模型实验。阶段1尚未整体通过；阶段2真实影像/LiDAR和阶段3发布/规模范围保留，搜索继续暂缓。

English: This closes one deterministic audit using389 preserved passes and65 failed-only passes. Historical six-combination first trials already exist; failures and unrun repetitions remain explicit. No inference, real Job, download, service restart or product source change occurred. Existing workflows remain frozen; any further verification needs a concrete change, problem, or fixed stage criterion.
