# 版本同步工作流

## 为什么需要这份文档

一次真实事故：2026-09-25 23:51 到 09-26 13:52 在本机磁盘上做了 **48 项改动**、
从未提交；09-26 23:55 到 09-27 00:57 又从别处推了 **3 个提交**到 `master`。两条线
改了同一批文件（10 个重叠，4 个真正冲突），其中一批还只存在于一块磁盘上、任何
分支和任何远端都没有备份。合并本身可以解决，但**风险本不该产生**。

根因不是 git，是流程：

1. 工作在磁盘上放了将近两天没有提交；
2. 期间没有任何一次 `fetch`，所以没人知道远端已经往前走了；
3. 仓库没有 `.gitattributes`，每台机器按各自的 `core.autocrlf` 检出不同的字节，
   把"代码冲突"和"行尾噪声"混在一起。

## 三条铁律

1. **动手前先同步。** 一条命令，退出码告诉你是否安全。
2. **做完一段就提交。** 不留过夜。提交在本地分支上是免费的，磁盘上不是。
3. **推送落到 `master`。** 远端默认分支是 `environment-module-state`，别往里推。

## 一、开始工作前

```powershell
cd forestry_runtime
.\scripts\git-sync.ps1
```

脚本会 `fetch` 并同时检查三件事，这三件事**必须分开看**：

| 检查 | 回答的问题 |
|---|---|
| 本地提交 vs 远端 | 远端有没有我还没有的提交？我有没有没推的？ |
| 工作区 vs 本地提交 | 有没有只存在于这块磁盘上的改动？ |
| 工作区 vs 远端 | 最严格的一致判定 |

退出码：`0` 三者一致；`1` 不一致（会列出缺哪些提交、哪些文件未提交）；`2` 远端不可达。

只看 `git status` 会漏掉"远端领先"，只看 `git log` 会漏掉"本地有未提交改动"——
这次事故正是这两者同时存在。

手动等价命令：

```powershell
git fetch origin
git status -sb
git log --oneline HEAD..origin/master     # 远端有、我还没有的
git log --oneline origin/master..HEAD     # 我有、远端没有的
git status --short                        # 工作区未提交
```

## 二、提交与推送

```powershell
git checkout master
git add -A                                # .gitignore 已挡住 .env / data / node_modules 等
git commit -m "这次改了什么"
git push
```

`.gitignore` 覆盖 `.env`、`data/`、`test-output/`、`node_modules/`、`frontend/dist/`、
`evaluation/work/`、`evaluation/results/`、`.venv-prosail/`、`release/`，所以
`git add -A` 不会带上密钥或几个 GB 的运行数据。

## 三、真的冲突了怎么办

先看清两边各自动了什么，再决定；不要用 `git checkout --ours/--theirs` 整文件选边，
那会静默丢掉一整侧的工作。

```powershell
git config merge.conflictStyle diff3      # 冲突标记里带上共同祖先，强烈建议
```

两类冲突的处理方式不同：

- **互补添加**（两边在同一锚点各加了不同东西）：保留两者。手动拼接，别选边。
- **契约指纹**（`tests/test_kernel.py` 里的 `EXPECTED_LEGACY_CONTRACT_SHA256`、
  `EXPECTED_VISIBLE_SCHEMA_CHARS`）：两边都改了工具契约，所以**两边的值都失效**，
  必须从合并后的代码重新测量，不能取任何一侧的数字。

不确定时，先把本地改动提交到一个分支保住，再去合并：

```powershell
git checkout -b wip/今天的改动
git add -A && git commit -m "wip: ..."
git checkout master
git merge origin/master
```

这样最差的结果也只是"没合并成功"，而不是"少了半天的工作"。

## 四、行尾

`.gitattributes` 固定了行尾：源码 LF，`.cmd`/`.bat`/`.ps1` 保持 CRLF。此前每次提交
都刷 `LF will be replaced by CRLF`，那是跨机器伪差异的来源。

如果历史文件的行尾仍不一致，可以在**没有其它改动时**单独归一：

```powershell
git add --renormalize .
git commit -m "chore: normalize line endings"
```

这会产生一个只改行尾的大提交，不要和其它改动混在一起。

## 五、本机代理的两个坑

本机 HTTPS 走 `127.0.0.1:7890` 的本地代理且会重新签证书，git 自带的 CA bundle 不认：

| 现象 | 处理 |
|---|---|
| `fetch`/`clone`/`push` 报 `self-signed certificate` | `git-sync.ps1 -NoVerifyTls`，或当次加 `-c http.sslVerify=false`；更彻底的办法是把代理 CA 装进系统信任链 |
| `push` 报 `remote rejected ... Internal Server Error` | 代理破坏了 HTTP/2 的 POST：`git -c http.version=HTTP/1.1 push`（本仓库已设为默认） |
