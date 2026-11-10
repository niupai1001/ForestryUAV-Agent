# V2 当前事实

核对日期：2026-10-25。状态：WP0进行中。

- 源码HEAD：3eb534011ece8a6df5d0ddc9ae19a075a536c7b6；本轮开始工作树干净。
- uv.lock与.venv包元数据：pydantic-ai-slim 2.54.0，pydantic-ai-harness 0.54.0。
- 获准在沙箱外只读检查localhost：8769/health返回连接拒绝；进程列表未见工作台Python服务。不启动或停止未知归属服务。
- Ollama 11434可访问，api/ps models为空。api/tags存在qwen3.5:4b（digest d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04）、qwen3.8:latest（digest aaee06c39dcf2437cde036998d960e1fc1494b8191be7cc9657d01e509097813）、qwen3.5:4b-cpu（digest 2ed9e6a2d93289bfdcb54a10d526aed8f043dafe24b8cc432cfba92315f9140f）。三者为Q4_K_M；模型声明262144不是应用实际请求窗口。
- 当前部署commit、实际请求Thinking/窗口未知；无请求故不能从驻留状态推定。
- Engine.run仍整回合持有model_slot；ToolRegistry无检索激活闭环；wait_job默认45秒；Jobs入口仍受诊断开关控制。
- Agent已注册TextOutput(public_answer)，旧output_recovery两项“所有普通文本应拒绝”的断言需结合产品契约裁决，不能直接恢复JSON强制要求。
- 既有科学成果和历史真实模型试次未重算；没有真实模型请求或科学Job新增。

环境差异：普通沙箱CIM不可访问、localhost socket被权限阻断；只读升权检查成功。基线第一次在pytest临时目录创建阶段错误，五项正文未执行，原始输出保留为baseline-environment-error-20261025.*。第二次仅更改临时目录与关闭pytest缓存，使用工作区专用目录，保持原五项输入和代码。
