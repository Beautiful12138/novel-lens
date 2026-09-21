# Embedding 候选与 CPU 验证

验证日期：2026-09-21。本文记录 Spec 10 的模型与运行时实验，不是当前 NovelLens 的安装前置步骤。现行服务不调用 embedding；独立配置验证和启动入口见[部署指南](部署指南.md#ai-协助准备-embedding)，向量存储与 HTTP / MCP 语义接口尚未实现。

## 结论与范围

Qwen3-Embedding-0.6B 的官方 Q8_0 GGUF 在 Windows CPU 上完成真实向量生成，可作为下一阶段接入候选。选择该候选的依据是模型体积较小、官方提供 GGUF、支持中文及查询指令，且能通过独立 llama.cpp 服务运行。没有完成多模型效果对比，不宣称其为最佳模型。

具体情境查询的自编样例表现好于写法查询。该结果支持继续验证原文语义召回，但不足以证明真实小说中的文学检索效果。沿用需求基线第 17 章：原文向量保持纯原文，写法说明与标签继续参与各自的检索，最终由 AI 回读原文判断适用性。

## 固定实验产物

| 项目 | 本次取值 |
| --- | --- |
| 模型仓库 | `Qwen/Qwen3-Embedding-0.6B-GGUF` |
| 不可变修订 | `370f27d7550e0def9b39c1f16d3fbaa13aa67728` |
| 文件 | `Qwen3-Embedding-0.6B-Q8_0.gguf` |
| 文件大小 | 639,150,592 字节，约 609.54 MiB |
| 模型 SHA-256 | `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439` |
| 运行时 | llama.cpp `b10964`，`--version` 为 `0.4.1-dev` / build 10964 / commit `b29c606e2` |
| Windows CPU 包 | `llama-b10964-bin-win-cpu-x64.zip` |
| 本地测得包 SHA-256 | `917f39c076402c421224824607397af20f53625a60defc20e8dd22446bf4c5d7` |
| 输出 | 1024 维，last pooling，L2 归一化 |
| 本次运行上限 | 4096 token、单 slot、CPU；未验证模型标称最大上下文 |

模型哈希与 Hugging Face 对应修订的 LFS SHA-256 一致。运行时包来自官方发行链接；表中运行时哈希是本次下载的本地摘要，没有额外核对上游发布签名或摘要。

来源：[Qwen 模型说明](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[固定 GGUF 修订](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/tree/370f27d7550e0def9b39c1f16d3fbaa13aa67728)、[固定版本服务说明](https://github.com/ggml-org/llama.cpp/blob/b10964/tools/server/README.md)。模型许可为 Apache-2.0；分发时保留上游许可要求。

查询按下面格式处理；小说片段不添加该前缀。这些规则与权重、量化方式、维度共同构成后续需要固定和校验的模型契约，不能只比较服务 URL 或向量维度。

```text
Instruct: Given a Chinese fiction writing query, retrieve relevant passages from novels
Query: <查询正文>
```

## 测试方法

硬件为 Intel Core Ultra 9 285H，16 核 / 16 逻辑处理器，31.5 GiB 系统内存；Windows 11 x64。集成显卡未参与推理。测试开始时可用内存约 7 GiB，期间其他桌面应用仍在运行；结果是当前机器的一次快照。

[样例](../tests/fixtures/embedding_cases.json)包含 24 段自编短文和两组各 12 个查询，每个查询预设一个目标片段。先测写法查询，观察到不足后保留原查询和标注，增加具体情境查询作为对照。情境查询更贴近已知原文，不能作为独立泛化测试，也不能用两组合计值掩盖写法组结果。Top 1 / Top 3 表示预设目标是否出现在对应名次内，不代表全部相关结果的召回率。

[测试脚本](../scripts/benchmark_embedding.py)通过 `/v1/embeddings` 实际生成向量，检查条目顺序、维度、有限值和归一化，以内积排序。报告保存样例摘要、候选 ID、排名、token 数及耗时，不保存小说、向量或请求全文。测试不访问 NovelLens 或数据库。

性能阶梯以模型 `/tokenize` 和 `/detokenize` 生成样例，分别截取 256 / 512 / 1024 / 2048 token 的内容，每档执行三次。每次在文本最前增加独立 UUID，避免重复输入与长度阶梯命中前缀缓存；实际请求还包含约 30 个前缀和特殊 token。JSON 的 `actual_tokens` 是该档最后一次请求值，三个请求的 UUID 分词长度可能稍有不同。

本次运行禁用 prompt cache 和 RAM cache。此前重复相同文本的预实验出现约 0.05 秒的缓存耗时，不能用于估算新文本处理速度；本文性能数据只使用修正后的测量。短查询耗时包含固定指令及本机 HTTP 往返，服务已预热；启动耗时也受操作系统文件缓存影响，不是首次下载或冷磁盘加载时间。

## 实测结果

4 / 8 / 12 / 16 线程四轮的目标排名相同：

| 查询组 | Top 1 | Top 3 |
| --- | --- | --- |
| 写法查询 | 6 / 12 | 7 / 12 |
| 具体情境查询 | 11 / 12 | 12 / 12 |

例如，“用记住对方习惯的小动作表达重逢感情”的目标排名第 4，“通过独有的小错误认出旧识”的目标排名第 12。增加具体场景后，前者排名第 1、后者第 2。这说明此样例集上查询表达显著影响候选排名，不应把模型相似度解释成理解了写作手法。

耗时单位为秒，长度阶梯列出三次请求的中位数及最小—最大值：

| 请求 | 4 线程 | 8 线程 | 12 线程 | 16 线程 |
| --- | --- | --- | --- | --- |
| 24 个短查询：中位数 / 最大值 | 0.118 / 0.248 | 0.083 / 0.186 | 0.067 / 0.125 | 0.083 / 0.164 |
| 256 token 内容 + 前缀 | 1.744（1.737—1.924） | 1.232（1.189—1.242） | 1.189（0.999—1.269） | 1.215（1.165—1.326） |
| 512 token 内容 + 前缀 | 3.206（2.886—3.215） | 2.355（2.293—2.882） | 2.060（2.004—2.093） | 2.379（2.323—2.654） |
| 1024 token 内容 + 前缀 | 6.843（6.754—7.100） | 4.532（4.499—4.809） | 3.803（3.784—4.425） | 5.333（4.528—5.903） |
| 2048 token 内容 + 前缀 | 14.621（13.571—14.909） | 9.734（9.662—10.122） | 8.319（7.565—8.493） | 9.401（9.194—9.938） |
| 4 个短片段单次批量请求 | 0.900 | 0.567 | 0.549 | 0.706 |
| 启动至健康检查成功 | 1.075 | 1.096 | 1.064 | 1.071 |

Windows `GetProcessMemoryInfo` 记录：加载就绪时四轮工作集均约 1.10 GiB，进程提交内存约 2.91 GiB；测试后峰值工作集为 3.47—3.48 GiB，进程提交内存约 4.11 GiB。工作集是驻留物理内存，提交内存包含可能换出的分配，两者不能混为模型权重大小。只记录进程指标，不代表整机的部署内存预算。

四轮均返回有效的 1024 维归一化向量。4 个短片段批量与逐条请求结果的最小向量内积约为 1，未发现本样例的差异；不据此承诺其他批量、精度或设备逐位一致。16,280 token 的超长样例请求在此配置下返回 HTTP 400；精确边界与错误映射还需在正式接入时验证。

本次 12 线程较快，1024 token 内容的耗时比 8 线程低约 16%；16 线程反而更慢，不能直接按逻辑处理器数量设置线程。本机下一轮可比较 8 / 12 线程在实际桌面负载下的响应与资源占用，不把单次实验最快值当作其他电脑的默认参数或全局最优。约 1000 token 的片段每条仍需数秒，全书首次构建可能耗时较长，需要保留显式构建、进度与中断接续。当前小样例不能准确预测某一本书的总耗时。

本次样例 SHA-256：`ad1a7d2801cc0cd1e726f7cccbe316b29782fa9c74fc0e0d246da0cbc6363002`。本机原始报告保存在已忽略的 `tmp/embedding-eval/q8-cpu-{threads}t-4096ctx-uncached.json` 及各自的 `.metadata.json` 中，其中 `{threads}` 为 4、8、12、16；不要求其他使用者持有这些本地文件，复现使用下述命令生成自己的报告。

## 在另一台 Windows CPU 机器复现

以下命令在仓库根目录、已按 README 准备 Python 的 PowerShell 中执行。只适用于开发者主动运行的实验，端口 18081 需空闲。下载约 610 MiB 模型及运行时包，文件放入已忽略的 `tmp/embedding-eval`；已有模型应先核对哈希后复用。

```powershell
$evalDir = Join-Path $PWD 'tmp/embedding-eval'
New-Item -ItemType Directory -Force $evalDir | Out-Null
$modelPath = Join-Path $evalDir 'Qwen3-Embedding-0.6B-Q8_0.gguf'
$runtimeZip = Join-Path $evalDir 'llama-b10964-bin-win-cpu-x64.zip'
curl.exe -fL --retry 3 -o $modelPath 'https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/370f27d7550e0def9b39c1f16d3fbaa13aa67728/Qwen3-Embedding-0.6B-Q8_0.gguf'
curl.exe -fL --retry 3 -o $runtimeZip 'https://github.com/ggml-org/llama.cpp/releases/download/b10964/llama-b10964-bin-win-cpu-x64.zip'
if ((Get-FileHash $modelPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne '06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439') { throw '模型校验失败' }
if ((Get-FileHash $runtimeZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne '917f39c076402c421224824607397af20f53625a60defc20e8dd22446bf4c5d7') { throw '运行时包与记录不符' }
Expand-Archive -LiteralPath $runtimeZip -DestinationPath (Join-Path $evalDir 'runtime') -Force
```

在此终端启动实验服务；使用 Ctrl+C 停止。比较线程时同时修改 `-t` 与 `-tb`，每轮停掉前一进程，不能并行争抢 CPU 后比较结果。

```powershell
& "$evalDir/runtime/llama-server.exe" -m $modelPath --embedding --pooling last --host 127.0.0.1 --port 18081 -t 4 -tb 4 -c 4096 -b 4096 -ub 4096 -np 1 -ngl 0 --device none --fit off --no-webui --log-disable --no-cache-prompt --cache-ram 0
```

在另一终端、同一仓库根目录运行：

```powershell
$env:PYTHONUTF8 = '1'
Invoke-RestMethod http://127.0.0.1:18081/health
uv run --locked python scripts/benchmark_embedding.py --label cpu-4t-4096ctx --report tmp/embedding-eval/cpu-4t-4096ctx.json
```

耗时受 CPU、内存压力、电源模式和后台负载影响，不能直接把本机线程数作为其他电脑的默认值。服务没有本实验之外的常驻或自动启动配置。

## 下一阶段与未验证内容

- 已将该模型契约、配置校验、真实试运行、保存和启动落实为独立入口。由 AI 按部署指南检查环境并选择参数，程序不自动探测或调参；资源判断不能只看总内存。
- 原始性能阶梯只覆盖约 2080 token，不能推导 4096 或 32768 token 的速度、峰值内存和效果。后续设计实验已验证 4095 token ID 输入成功、4096 被拒绝，并选择首版 1024 目标／128 重叠；真实小说中的参数效果仍待验证。
- 后续设计实验发现字符串默认解析会把原文中形似特殊 token 的文本当作控制标记，因此业务适配固定 `add_special=true, parse_special=false` 后传 token ID。原有普通自编样例记录仍保留，不把它们当作特殊标记输入的验证。
- pgvector 0.8.6 已完成与 PostgreSQL 18.6 / PGroonga 4.0.8 的隔离共存及恢复验证；正式镜像、可接续索引、迁移和 HTTP / MCP 尚待实现，共享库未变。设计与补充实验见 [Spec 10](../specs/10-原文语义检索与Embedding部署.md#9-索引设计阶段验证)。
- 在可用 GPU 机器验证驱动、加载、内存及相同权重的跨设备一致性。Linux、macOS、GPU、其他量化精度均未完成本次推理实测。
- 真实小说、独立质量样例、长时间运行、并发、全书构建及实际 AI 宿主流程仍待验证。需要更换模型时显式更改契约并重建向量，不能静默替换。
