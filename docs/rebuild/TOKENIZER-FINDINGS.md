# Qwen35本机分词事实

状态：文本BPE已接入工作树；模板/图像仍估计，尚未部署。日期：2026-10-25。

## 模板接续

已读取GGUF chat_template，并通过Jinja2 SandboxedEnvironment渲染实际wire消息/工具的副本，规范工具调用arguments字符串为JSON映射、处理特殊token、记录模板hash与思考开关。原请求不修改。文本模板计量明确estimated；包含图片/视频时仅给文本占位计数，total_tokens=None、视觉unknown，不把占位符当完整图像成本。未知/不支持模板也返回unknown，保留原PreparedInput视觉边界估计。

一次新的chat校准复用原SDK请求的12个工具定义，raw=false、think=false、num_ctx32768、num_predict1；与已冻结raw试次共2次请求，总预算2次已用尽。提供方2938输入tokens，本地渲染3155，含10%余量3471覆盖本样本；exact_template_match=false，不能宣布本地完整模板与服务完全相同。报告workspace/template-calibration-20261025/report.json及docs/rebuild/template-calibration-report-20261025.json，前后模型列表为空。请求、SDK原字节与渲染文本按原hash封存，没有再调模型。

离线序列化排查（template-serialization-analysis-20261025.json）显示紧凑JSON计2743、带空格3155，均不等于服务2938；未按单一样本调参凑相等，保留覆盖本样本的估计与偏差。Contexts.measured仅在明确无视觉且provider用量有效时记录模板差额；有视觉或usage缺失时不推导视觉成本，不记为0。

实际请求发送前模板估计如超过窗口，将先持久保存信封、计量与wire_rejected事件再阻止传输；模板部分未知时不据此宣称完整预算。软件模板/视觉未知/沙箱边界、超预算拒绝证据、实测差额归因均已定向验证。

本地Ollama qwen3.5/4b manifest存在，model层指向sha256:a9d0a3e1d8c91732a3812becd31f8d4a8ff57414903f226306069f0437e2ac50。只读取本机GGUF头部元数据，没有下载/加载模型或读取tensor数据。文件GGUF v3，tokenizer.ggml.model=gpt2，tokenizer.ggml.pre=qwen35，eos_token_id=248046，add_bos_token=false，元数据末端偏移10942739。服务声明上下文与实际应用请求窗口仍须分开。

当前.venv有tiktoken 0.14.0，未见transformers/tokenizers/gguf分词库，也未在所查HuggingFace缓存发现Qwen tokenizer。不能直接用现有GPT编码冒充本模型tokenizer。

官方源码对照：llama.cpp/src/llama-vocab.cpp的LLAMA_VOCAB_PRE_TYPE_QWEN35分支在字母分组中包括Unicode组合标记p{M}，与Qwen2分支不同。已经通过GitHub connector读取；尚未将该regex或GGUF BPE数据移植到本项目。框架JSON序列化也不等于服务chat template渲染，所以即使文本BPE接通，图像、模板和工具边界仍应区分实测/估计/未知。

已实现runtime/tokenization.py：核对冻结Ollama manifest digest与模型层身份，仅读取有界GGUF元数据，验证gpt2/qwen35词表字节完整性、类型与merge顺序，复用已安装tiktoken，不读取模型tensor。词表按路径/大小/mtime缓存。没有匹配资产/身份、格式不支持或损坏时明确回退，不使用GPT等其他编码冒充Qwen。tiktoken 0.14原为已锁定间接依赖，本次离线增加直接声明，没有安装/下载或升级版本。

PreparedInput的文本、schema与指令增量及既有压缩能力现在可使用同一个模型counter。输出记录text_tokenizer_identity和模板估计性质；BPE结果加10%文本余量，framing另留位，仍标estimated而非actual。实测usage锚点与分项不重复付费的原协议保持。没有匹配本地资产的旧fixture仍回退原字节上界，不将其作为已校准模式。

唯一真实raw校准结果：workspace/tokenizer-calibration-20261025/report.json（摘要复制docs/rebuild/tokenizer-calibration-report-20261025.json），冻结qwen3.5:4b digest d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04；中英/组合字符/数字/工具JSON混合文本6162字节，本地BPE1684 tokens，provider prompt_eval_count同为1684，含文本余量1853。1次generate、1个输出token、0科学Job；前后驻留模型列表均为空。raw=true没有chat template，此结论不覆盖完整模板/图像，也不代表真实任务成功率。

两条原上下文回归的完整原样本，在显式绑定正确本机目标tokenizer、保持软件模型响应的配置下通过（qwen-context-20261025.xml）；不同配置与历史无身份字节模式不能混作相同试次。下一步仍须完整模板/视觉计量与真实任务接缝，不能只凭文本BPE宣称全部预算问题结束。
