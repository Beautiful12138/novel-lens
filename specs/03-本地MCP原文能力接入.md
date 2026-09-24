# 03 本地 MCP 原文能力接入

状态：已实现，Windows 上已通过官方 MCP 客户端及真实 PostgreSQL 验证。MCP Streamable HTTP 集成到现有 FastAPI 应用，在本机以一个服务进程、一个监听端口统一提供 REST 与 MCP 能力，共用 Python 业务层和数据库连接池。实际 AI 客户端宿主尚未配置或联调；该兼容性边界与已完成的协议验证分别记录。

依据：[需求与设计基线](../docs/小说分析与创作知识服务_需求与设计基线.md)第 4、6、11、22–23 章、[Spec 01](01-工程基础与运行约定.md)、[Spec 02](02-小说导入与原文读取.md)及根目录 AGENTS.md。

## 1. 目标与边界

支持具备 MCP Streamable HTTP 能力的本地 AI 客户端完成“选择一份结构已确认的 TXT → 预检和导入 → 获取作品与目录 → 按稳定坐标读取与补读 → 查询或重放导入结果”。AI 负责结构确认与文学判断，MCP 仅暴露数据工具。

运行关系：

```text
NovelLens 本地应用进程（默认 127.0.0.1:8000）
├─ /health、/works、/work-imports 等现有 REST 路由
├─ /mcp：AI 客户端连接的 Streamable HTTP 端点
│   └─ 文件工具按路径读取任意目录中有权限访问的普通文件
└─ 共用 ImportService / ReadingService 和数据库连接池
    └─ 远程 PostgreSQL（公网直连，密码认证，关闭 TLS）
```

应用与小说文件位于同一主机，文件路径以应用进程看到的文件系统为准；Windows 和 WSL 路径不能直接互换。应用启动一次即可提供两类接口，MCP 不启动第二个业务进程，也不在内部通过 HTTP 请求现有 REST 路由。数据库继续作为独立基础设施运行，“一个服务进程”不表示把 PostgreSQL 嵌入 Python 进程。

本期不包含远程部署、stdio 入口、业务 CLI、Web、分析标注、搜索、模型、Skill 工作流、文件格式整理、后台导入队列或客户端全局配置修改。不为未实现能力注册空工具。整文件下载继续使用 Spec 02 的 REST 接口，与 MCP 位于同一服务；本期不新增 MCP 文件导出工具，也不把整本文件作为 MCP 文本或 Base64 返回。

## 2. 协议与运行

采用官方 `modelcontextprotocol/python-sdk` 的稳定 2.x 系列，已验证的 2.2.0 写入 uv.lock。不自行实现 JSON-RPC，不额外引入第三方 MCP 框架或 SDK 的 CLI 开发工具集。使用 SDK 的公开低层工具处理器显式校验参数并隐藏底层错误，协议解析和传输仍由 SDK 负责。官方 SDK 基础工具、传输能力见文末来源；具体 AI 客户端宿主兼容性仍需接入时验证。

沿用模块入口 `uv run --locked python -m novel_lens`，无需新增启动模式。依赖环境和数据库迁移按现有 README 准备；应用启动不自动迁移或启动 Docker。服务启动后，AI 客户端连接 `http://127.0.0.1:8000/mcp`，不再通过客户端配置启动一个 MCP 子进程。

使用官方 SDK 的可挂载 ASGI 应用集成到 FastAPI。外部端点固定为 `/mcp`，避免挂载前缀叠加成 `/mcp/mcp`；具体协议方法由 SDK 处理。宿主 FastAPI 的 lifespan 显式管理 MCP 会话管理器和已有业务资源，不能假设挂载的子应用会自动运行自己的 lifespan。

当前工具不依赖协议会话存储，采用 SDK 的无状态 HTTP 与 JSON 响应模式；请求键和作品等业务状态仍持久化到 PostgreSQL。MCP 应用最后挂载到根路径，保留其内置 `/mcp` 路由及现有 REST 路由优先级。

- 工具发现不要求数据库在线；实际依赖数据库的调用失败时返回明确错误。
- MCP 协议响应经 HTTP 返回，应用 stdout 保持无输出；日志、配置错误写 stderr，不回显正文、文件完整内容、SQL 参数或凭据，继续关闭访问日志。
- 一个应用实例创建一组业务服务和数据库连接池，由 REST 与 MCP 共同使用；连接资源随应用生命周期关闭。多个客户端通过数据库约束共享数据，不为每个 MCP 客户端再建业务服务或连接池。
- 客户端断开只结束对应协议连接或会话，不停止 NovelLens，也不影响其他客户端；停止应用时统一结束 MCP 生命周期并释放数据库资源与监听端口。
- 同步文件和数据库操作不得长时间阻塞协议事件处理；采用 SDK 支持的执行方式或有界工作线程，不创建独立任务系统。
- 协议握手、版本协商、工具调用与退出均通过官方客户端实际验证；不能只直接调用 Python 工具函数作为接入成功的依据。

## 3. 配置与本地文件导入

沿用现有数据库、日志、文件大小、HOST 和 PORT 配置，以及环境变量优先于当前工作目录 .env 的规则。REST 与 MCP 共用 HOST / PORT，不新增 MCP 端口或后端 URL 配置；`NOVEL_LENS_MAX_REQUEST_BYTES` 继续限制整个应用收到的 HTTP 请求体，包含 MCP 协议请求，`NOVEL_LENS_MAX_FILE_BYTES` 限制实际导入文件。

本增量只支持回环监听，默认 127.0.0.1；HOST 可使用回环 IP 或 localhost，非回环绑定配置在监听前拒绝。该限制已同步配置校验、Spec 01 相关说明与示例。MCP 端点按 SDK 传输安全机制校验 Host，以及存在时的 Origin；仅允许与已配置本机地址和端口对应的来源，不开放通配 CORS。无 Origin 的合法本机非浏览器客户端可以调用。本期仍是本机可信调用，不增加用户账号或远程认证体系。

文件工具不配置导入根目录，不设置目录白名单。服务进程有权限读取的任意目录均可作为文件来源；操作系统权限仍然生效。

预检和导入工具接收 `file_path`，支持应用所在操作系统的绝对路径和相对路径。相对路径以应用进程工作目录为基准，可以包含 `..`；推荐使用绝对路径，例如 `D:/小说/样例.txt`。不接收正文字符串、Base64、URL 或第二份文件，也不接受一个 confirmed 开关替代正文校验。

文件读取规则：

1. 按操作系统规则解析路径，允许绝对路径、上级目录路径及指向其他目录的符号链接或目录联接。解析后的目标须为可读的普通文件；文件不存在、不可读或不是普通文件时返回明确错误。不绕过操作系统权限，也不自动创建目录。
2. 以二进制读取，累计大小不得超过现有 max_file_bytes，不能仅依赖文件大小声明后无界读取。一次调用取得一份有界字节内容，再传给 ImportService；不解码后重新编码，不修改或覆盖素材。
3. 调用方应保持文件在读取期间不变；若检测到读取期间文件变化，拒绝本次导入并返回 `SOURCE_FILE_CHANGED`。本期不提供与外部编辑器协同的文件快照或锁定机制。
4. UTF-8、BOM、换行、书名、自然段、字节位置、文件摘要和格式上限全部沿用 Spec 02。MCP 不再实现一套解析规则，也不要求调用方提交整理前的文件。
5. 预检是独立调用，不预留名称、不锁定文件。正式导入重新读取并校验当次字节；调用方不得把先前预检视为对随后修改文件的确认。

这是一项仅属于本地 MCP 工具的文件读取能力。Spec 02 的 REST 上传接口仍只接受 multipart 文件字节，不新增服务器路径参数；两种入口最终调用相同的字节导入服务。作品入库后不依赖导入路径继续存在，文件路径不作为作品身份或请求指纹。

## 4. 工具契约

工具使用下划线命名，描述与参数说明使用简体中文。JSON Schema 和运行时校验保持一致，拒绝未知参数，UUID、分页上限与范围规则不得因未经过 REST 参数校验而失效。复用现有 contracts 中的类型和约束，必要的共用校验放在可被两种入口复用的位置。

下表中的 UUID 参数均必填；分页参数 limit 默认 100、范围 1–1000，cursor 默认 null。只有表中标为可选的参数可以省略。

| 工具 | 输入 | 结果 / 业务映射 |
| --- | --- | --- |
| `work_import_validate` | file_path | ValidationReport；ImportService.validate |
| `work_import` | file_path、request_id | ImportOut；ImportService.import_source |
| `work_import_get` | request_id | 已提交 ImportOut；ImportService.result |
| `work_list` | 可选 limit、cursor | Page[WorkOut]；默认排除屏蔽作品，详见 Spec 11；ReadingService.list_works |
| `work_get` | work_id | WorkOut；ReadingService.get_work |
| `source_sections` | work_id；可选 limit、cursor | Page[SectionOut]；ReadingService.list_sections |
| `source_paragraphs` | work_id、section_id；可选 limit、cursor、format | 完整或精简段落页；ReadingService.list_paragraphs |
| `source_read` | source_range；可选 limit、cursor、format | 完整或精简范围页；从 source_range.work_id 确定作品，调用 ReadingService.read |
| `source_get_context` | work_id、section_id、paragraph_id；可选 before、after、format | 完整或精简上下文；ReadingService.context |

SourceRange 的 work_id、section_id、start_paragraph_id、end_paragraph_id 均必填。before / after 默认 0，各为 0–100，不跨 Section。工具名以此表为准，不同时注册点号别名或同义工具。

读取结果沿用 Spec 02 的字段、分页游标与实际范围，不擅自总结正文、合并段落、改写位置或扩展到其他作品。空列表与资源不存在保持区别。

上述三个原文工具支持 Spec 02 的 `format=full|compact`，默认 compact 供 AI 连续阅读，字节定位显式使用 full。工具发现应声明两种成功输出及错误结构；1 MiB 上限按所选格式的最终业务对象计量。工具说明须区分分页上限和读取范围：增大 limit 不会越过 SourceRange，before / after 是段落数，不保证场景完整。

工具注解按实际副作用设置：只有 work_import 会写入数据库，其余工具不修改小说或业务数据。work_import 不覆盖作品，幂等性以相同 request_id 和相同字节为前提；工具描述必须说明重试方式。注解只是客户端提示，不替代运行时校验和数据库约束。

## 5. 结果、错误与读取体积

成功调用通过 structuredContent 返回相应业务对象，同时提供同一对象的 JSON 文本表示，供只读取文本的客户端使用；不附加另一份散文总结。声明与实际结果对应的 outputSchema，错误结构也纳入契约，避免将错误当作成功模型校验。

工具执行失败设置 isError=true，返回 `code`、`message`、`details`。未知工具或不合法的 MCP 消息按协议错误处理；已识别工具的参数、文件、业务和数据库错误使用工具错误。预检完成但发现格式问题属于成功取得 ValidationReport，isError=false、status=invalid。

HTTP 请求体超限、Host / Origin 不允许等在协议执行前发生的拒绝使用相应 HTTP 传输错误；不得为了返回 isError 而绕过 HTTP 接收边界。客户端需区分传输失败、协议错误和已取得的工具结果。

| 情形 | 错误处理 |
| --- | --- |
| 参数缺失、类型或范围错误、额外参数 | INVALID_INPUT，不回显原始参数值 |
| 文件不存在 | FILE_NOT_FOUND |
| 文件不可读或不是普通文件 | FILE_UNREADABLE |
| 读取期间检测到文件变化 | SOURCE_FILE_CHANGED |
| 文件超限 | 沿用 FILE_TOO_LARGE |
| 格式无效、重名、请求键冲突、坐标与游标错误 | 沿用 Spec 02 的业务错误及 details |
| 数据库不可连接或配置缺失 | DATABASE_UNAVAILABLE |
| 其他数据库错误 | DATABASE_ERROR，不暴露 SQL 或驱动异常原文 |
| 其他未预期的执行错误 | INTERNAL_ERROR，日志仅记录异常类型，不暴露底层异常文本 |
| 读取结果过大 | RESULT_TOO_LARGE，说明缩小读取范围的方式 |

共享的业务与数据库错误分类不得只留在 FastAPI 的 REST 异常处理器中；REST 与 MCP 使用相同语义，各自转换成对应协议的错误响应，MCP 不把驱动异常直接返回给 AI。

为限制长篇内容进入上下文，每次工具调用的业务对象 JSON 以 UTF-8 编码计，最多 1 MiB；使用不转义非 ASCII 字符的紧凑 JSON 序列化计量。兼容文本是该对象的同内容副本，协议消息还包含这份副本及封装开销，该上限不等于整个协议消息大小或 token 数。

超限时返回错误，不悄悄截断自然段、不把未返回内容标记成已读取。调用方可减小 limit、before / after 或缩小 SourceRange；单个完整段落仍超过上限时明确说明本期 MCP 无法返回该段，不建议无效的重复调用。同服务的 REST 读取契约不受这一 MCP 结果限制影响。该上限仅控制返回给客户端的结果，不宣称降低既有业务查询的峰值内存。

## 6. 幂等、取消与结果恢复

request_id 由调用方在正式导入前生成并保存；MCP 不偷偷生成或替换请求键。相同键及字节重放返回相同作品和坐标、replayed=true；相同键换内容及新键同名冲突继续由事务与数据库唯一约束裁决。

调用超时、取消、连接断开或应用进程退出不等于数据库一定回滚。调用方应先通过 work_import_get 查询，再决定是否使用原键、原文件重试；IMPORT_NOT_COMMITTED 仅表示查询时没有已提交结果。连接中断时不得承诺“导入失败且未写入”，也不得新建请求键自动重试。

本期沿用同步导入，不新增 accepted / running 等任务状态或虚构进度。MCP 协议请求 ID、协议会话与业务 request_id 是不同概念；业务结果不绑定客户端连接，恢复依据是持久化的业务 request_id。

## 7. 实施范围

在现有 src/novel_lens/ 中增加实际需要的 MCP ASGI 集成、工具适配和本地文件读取逻辑，并纳入现有 create_app 与统一启动生命周期；依赖及配置通过现有 pyproject.toml、uv.lock、config.py 和 .env.example 维护。不创建独立 MCP 启动入口、重复业务服务或新的数据库结构。

实现覆盖以下接入点：

- 读取服务部分方法接收普通 Python 参数，不能依赖 FastAPI 才执行分页和 UUID 校验；MCP 调用前须使用共用约束校验。
- SQLAlchemy 异常公开化由共享错误分类完成，REST 与 MCP 复用分类，保持无敏感信息输出。
- 文件路径是新增的本地输入边界，须先转成有界字节，再进入已有 ImportService。
- 挂载路径、MCP 生命周期、Host / Origin 校验及现有请求体上限需共同验证，确保不影响原有路由，也不产生重复服务实例。

完成后更新 README 的统一启动方式、客户端 URL 配置示例、依赖准备和验证记录。应用配置与客户端配置区分：应用持有数据库配置，客户端只配置本机 MCP URL，不需要数据库密码或 Python 子进程命令。密码只在被忽略的本地配置或进程环境中提供，不写入示例。只提供客户端配置说明，不自动安装插件或修改用户全局客户端配置。

## 8. 验收条件

| 编号 | 可观察行为与验证 |
| --- | --- |
| M1 | 只启动一个真实 NovelLens 应用进程，/health、现有 REST 路由和 /mcp 在同一端口可用；官方 Streamable HTTP 客户端完成协商、发现本规格九个原文工具并按 schema 调用；Spec 04 增加九个资产工具，Spec 05 增加十一个实体与关系工具 |
| M2 | 以隔离目录中的中文路径小样例完成预检、导入、目录、段落、范围和上下文读取；BOM、换行、空白及字节坐标与 Spec 02 一致 |
| M3 | 真正访问 PostgreSQL，REST 导入后 MCP 可读取、MCP 导入后 REST 可查询和下载同一原文；跨入口重放返回原 UUID，同键异内容、异键同名及跨作品 / Section 引用不能绕过现有规则 |
| M4 | 应用进程重启后可查询原请求和读取原坐标；忽略首次导入响应后仍可用持久化请求键恢复，不重复创建作品 |
| M5 | 无需目录配置即可通过绝对路径、相对路径、上级目录路径及跨目录链接或联接读取普通文件；目录、不可读文件与超限文件返回对应错误；失败不改素材、不产生部分作品；平台受限的检查明确记录 |
| M6 | 参数上限、未知字段、错误游标、非法坐标通过实际 tools/call 验证；读取结果超限返回错误而非截断正文 |
| M7 | 数据库未配置或不可用时 /health 和工具发现仍可用，相关业务调用返回安全错误；stdout 无应用输出，stderr 无正文、凭据或 SQL 参数 |
| M8 | 关闭一个 MCP 客户端后应用、REST 及另一个客户端继续可用；停止应用后生命周期、数据库资源与端口正常释放；受影响的原有回归通过 |
| M9 | 非回环绑定配置被拒绝；不允许的 Host / Origin 以及超限请求体不能到达工具执行；合法本机客户端无 Origin 时可调用，/mcp 挂载不遮蔽原有路由 |
| M10 | 三个原文工具默认 compact，与 HTTP 返回一致，保留正文、稳定 ID 和实际范围；可重建 SourceRange 并跨格式续读，显式 full 返回完整字段，空章节、非法格式及结果超限处理正确 |

测试使用独立数据库与小型文件，不导入完整小说、不修改 data/ 内已有素材。Windows 运行应用、客户端连接本机 HTTP MCP 为主要验收环境；如交付 WSL 应用启动示例，必须实际验证该路径，未验证环境不写成已支持。真实 AI 客户端宿主联调与官方协议客户端测试分别记录，不以其中一种代替另一种。

完成条件：工具已实现、上述必要验证通过、文档与配置同步，才能将本规格标记为已实现。

### 实际验证记录

2026-09-18，Windows Python 3.12.13、官方 MCP SDK / Client 2.2.0、WSL Docker PostgreSQL 18.6：任意目录读取规则的 21 项相关回归均通过，无跳过，覆盖 MCP、配置、本地文件读取和真实 HTTP 进程；Ruff、格式检查和 mypy 通过。有一项 Starlette 使用 AnyIO 旧别名的上游弃用提示。

- `tests/test_mcp_http.py` 启动真实应用进程，通过官方 HTTP 客户端发现并调用九个工具；验证同端口 REST 互通、原字节与稳定坐标、跨入口幂等和冲突、客户端断开及进程重启后恢复。
- 文件测试验证无需目录配置即可通过绝对路径、相对路径、上级目录和 Windows 跨目录联接预检及导入；同一文件经不同路径重放同一请求键，返回相同作品和字节。目录、被强制锁定的不可读文件及超限文件仍被拒绝，失败请求无已提交结果。`tests/test_local_files.py` 验证读取期间真实文件变化的拒绝行为。
- 通过真实工具调用验证参数、范围、错误游标、未知字段及 1 MiB 结果上限；验证数据库不可用时仍可发现工具，以及 Host / Origin 拒绝和无 Content-Length 的请求体超限。
- 真实进程验证 stdout 为空、日志不含测试正文或凭据；通过 Windows Ctrl+Break 正常关闭，观察到应用生命周期关闭完成，随后重新绑定原端口成功。原文事务和并发行为的验证记录见 Spec 02，本次路径调整未修改相关实现。
- 测试使用当次独立数据库及临时素材，未导入开发小说。此记录仅覆盖 Windows 应用的 HTTP MCP；未验证 WSL 应用的 MCP 接入、实际 AI 客户端宿主、长篇吞吐或创作效果。

2026-09-23（精简阅读增量）：

- 在 WSL 专属 PostgreSQL 18.6 容器及随机测试库中，Windows 应用通过官方 MCP 客户端与真实 HTTP 完成 25 项相关测试，无跳过；覆盖精简格式、原文读取、MCP、进程恢复、请求体限制与原文解析。存在一项既有 Starlette / AnyIO 弃用提示。
- 验证 full / compact 的正文与稳定 ID 一致、三个入口的 HTTP / MCP 投影一致、范围重建、跨格式续读、空章节与上下文边界，以及非法格式、跨作品 / 章节、反向范围、错误游标和超大段落拒绝。Ruff、格式检查、严格 mypy 和写作 Skill 格式检查通过。
- 测试记录位于忽略目录 `tmp/compact-reading-tests.xml`；当次测试库、容器、匿名卷和应用进程已回收。没有迁移数据库、写入共享业务库、重启现有联调服务或执行 Git 提交推送。实际 AI 宿主采用精简格式后的读取策略、token 成本和创作效果尚待验证。

## 9. 官方参考

- [官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)：SDK、支持的传输与版本说明；实现时锁定实际验证的依赖。
- [官方 SDK 集成到现有 ASGI 应用](https://py.sdk.modelcontextprotocol.io/run/asgi/)：FastAPI / Starlette 挂载、外部路径与生命周期管理。
- [MCP HTTP 传输约定](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http)：Streamable HTTP、本机监听和 Origin 校验。
- [MCP 工具契约](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)：工具 schema、结构化结果、注解及执行错误；协议兼容由 SDK 和实际协商验证。
