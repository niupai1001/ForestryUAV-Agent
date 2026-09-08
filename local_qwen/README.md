# 本机 Qwen3.5 4B

当前部署使用 Ollama v0.33.2 和官方 `qwen3.5:4b` 模型，API 仅监听本机地址。

## 已部署位置

- Ollama：`E:\AI\Ollama`
- 模型：`E:\AI\OllamaModels`
- 日志：`E:\AI\OllamaLogs`
- API：`http://127.0.0.1:11434`
- 默认上下文：32K

用户环境变量 `OLLAMA_MODELS`、`OLLAMA_HOST` 和 `OLLAMA_CONTEXT_LENGTH` 已设置，`E:\AI\Ollama` 也已加入用户 PATH。重新登录 Windows 后，新的终端会自动读到这些值，并可直接使用 `ollama` 命令。

## 使用

推荐直接使用不受 PowerShell 脚本策略限制的 `.cmd` 入口：

```powershell
# 启动本地服务（重启 Windows 后需要执行一次）
.\start_qwen.cmd

# 进入交互对话，输入 /bye 退出
.\chat_qwen.cmd

# 测试本地 API
.\test_qwen.cmd
```

这些入口只为当前启动进程设置 `ExecutionPolicy Bypass`，不会修改系统或用户的 PowerShell 执行策略。

如果系统允许直接执行 PowerShell 脚本，也可以运行：

```powershell
# 启动本地服务（重启 Windows 后需要执行一次）
.\start_qwen.ps1

# 进入交互对话，输入 /bye 退出
.\chat_qwen.ps1

# 重新登录后，也可以在任意新终端直接运行
ollama run qwen3.5:4b

# 测试本地 API
.\test_qwen.ps1

# Python API 示例（只使用标准库）
python .\example_chat.py
```

Ollama 还提供 OpenAI 兼容入口：`http://127.0.0.1:11434/v1`。后续 Agent 框架如果支持 OpenAI-compatible provider，可把 base URL 指向该地址，模型名填写 `qwen3.5:4b`；本地 Ollama 不校验 API key，但某些客户端要求提供非空占位值。

## 显存与上下文

当前机器的 RTX 4060 Ti 16GB 可让该模型以 100% GPU 方式运行。32K 上下文实测模型驻留约 4.2GB 显存。模型标称支持更长上下文，但上下文越长，KV cache 占用和首字延迟越高；Agent 开发阶段建议先保持 32K，并在单次请求的 `options.num_ctx` 中按需调整。
