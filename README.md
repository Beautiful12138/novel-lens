# NovelLens

小说分析与创作知识服务。外部 AI 负责文学判断，服务负责原文、分析资产和检索等稳定数据能力。

已实现 [工程基础](specs/01-工程基础与运行约定.md)、[小说导入与原文读取](specs/02-小说导入与原文读取.md)、[本地 MCP 接入](specs/03-本地MCP原文能力接入.md)和 [分析标注与标签管理](specs/04-分析标注与标签管理.md)：接收一份结构已确认的 TXT，保留原字节和固定坐标，支持保存、查找和修订带原文引用的写法说明，以及共享标签和写入结果恢复。实体、跨章节关系、风格导航、分析进度、全文与语义检索、模型、Web 和业务 CLI 尚未实现。

FastAPI 应用同时提供 REST 接口与 `/mcp`，共用一个进程、监听端口、业务层和数据库连接池。MCP 客户端通过本机 URL 连接；应用使用统一启动命令。本期只支持本地使用。

下一增量见 [Spec 05：实体与跨章节关系](specs/05-实体与跨章节关系.md)，当前为待确认草案；新增实体、关系和标注实体关联尚未实现。

## 安装与运行

需要 Python 3.12、uv。从项目根目录执行：

```text
uv sync --locked
uv run --locked python -m novel_lens
```

默认 `127.0.0.1:8000`，单进程运行，无热重载，Ctrl+C 停止。`GET /health` 返回 `{"status":"ok"}`，只表示进程可响应。启动不连接数据库、不读取小说，也不下载模型；业务接口需要下述数据库配置和迁移。

依赖安装到项目 `.venv`。Windows 与 WSL 不共用虚拟环境；WSL 可将 `UV_PROJECT_ENVIRONMENT` 指向 `tmp/venv-linux`。需要限定缓存和 Python 下载目录时，在当前终端设置 `UV_CACHE_DIR`、`UV_PYTHON_INSTALL_DIR` 指向项目 `tmp/` 下的目录，不修改全局配置。`--locked` 拒绝过期锁文件；调整依赖后用 `uv lock` 更新。

## 配置

可将 `.env.example` 复制为项目根目录 `.env`，使用 UTF-8，不提交版本管理。

| 配置键 | 默认值 | 说明 |
| --- | --- | --- |
| `NOVEL_LENS_HOST` | `127.0.0.1` | 仅允许回环 IP 或 localhost；不允许 0.0.0.0 等非回环绑定 |
| `NOVEL_LENS_PORT` | `8000` | 1–65535 |
| `NOVEL_LENS_LOG_LEVEL` | `INFO` | DEBUG、INFO、WARNING、ERROR、CRITICAL |
| `NOVEL_LENS_DATABASE_URL` | 未设置 | `postgresql+psycopg://用户:密码@地址:端口/数据库`；密码须 URL 编码 |
| `NOVEL_LENS_MAX_FILE_BYTES` | `67108864` | 单文件上限，64 MiB |
| `NOVEL_LENS_MAX_REQUEST_BYTES` | `68157440` | 整个 HTTP 请求体上限，65 MiB，包含 multipart 开销，也限制 MCP 请求 |

优先级为进程环境变量、当前目录 `.env`、默认值；不搜索上级目录。`.env` 中未知配置键使启动失败，其他进程环境变量忽略。配置错误只显示字段及规则，不回显输入值。日志写 stderr，关闭 HTTP 访问日志，不记录完整正文、请求或 SQL 参数。

服务面向本机可信调用，无账号鉴权，仅允许监听回环地址。MCP 校验 Host 和存在时的 Origin，限定为本机地址及配置端口；非浏览器客户端可以不带 Origin。SDK 的协议诊断日志关闭，以免输出完整请求；项目层保留安全的错误信息。

## 本地开发数据库

[compose.yaml](compose.yaml) 使用固定摘要的 PostgreSQL 18.6 官方镜像，在 WSL Ubuntu 24.04 Docker 中运行独立实例。

| 项目 | 值 |
| --- | --- |
| Windows / WSL 地址 | `127.0.0.1:55432` |
| 数据库 / 初始化管理账号 | `novel_lens` / `novel_lens` |
| 本地密码文件 | `data/postgres/password`，已忽略 |
| 容器 | `novel-lens-postgres` |
| Docker 命名卷 | `novel-lens-postgres-data` |

新 checkout 首次启动时，在项目根目录的 PowerShell 生成密码文件；已有文件保留：

```powershell
$dbSecretPath = Join-Path $PWD 'data/postgres/password'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dbSecretPath) | Out-Null
if (-not (Test-Path -LiteralPath $dbSecretPath)) {
    $dbPassword = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    [System.IO.File]::WriteAllText($dbSecretPath, $dbPassword, [System.Text.UTF8Encoding]::new($false))
}
wsl -d Ubuntu-24.04 --exec docker compose up -d --wait postgres
```

配置当前 PowerShell 会话、显式执行迁移，然后启动业务服务：

```powershell
$dbPassword = [Uri]::EscapeDataString((Get-Content -LiteralPath 'data/postgres/password' -Raw).Trim())
$env:NOVEL_LENS_DATABASE_URL = "postgresql+psycopg://novel_lens:${dbPassword}@127.0.0.1:55432/novel_lens"
uv run --locked alembic upgrade head
uv run --locked python -m novel_lens
```

应用不会自动建表或迁移；数据库连接配置未设置时 REST 业务请求返回 503，MCP 返回 DATABASE_UNAVAILABLE。数据库缺少业务表时返回数据库操作失败，需先执行迁移。迁移 0001 建立 works、work_sources、sections、paragraphs；0002 新增 tags、annotations、annotation_ranges、annotation_tags、asset_write_requests。已有原文库可执行 `uv run --locked alembic upgrade head` 升级，保留原文件和坐标。未安装 pgvector 或 PGroonga。

停止和恢复数据库：

```powershell
wsl -d Ubuntu-24.04 --exec docker compose stop postgres
wsl -d Ubuntu-24.04 --exec docker compose start postgres
```

WSL 内进入项目后可直接运行 Docker 命令。完整上传文件存入 PostgreSQL `work_sources.content`（bytea），不是另存到 Windows 的 `data/`。数据库数据保存在命名卷，挂载于容器 `/var/lib/postgresql`。`docker compose down` 保留命名卷，`down -v` 删除数据库数据；已有数据卷的密码不会随密码文件变化自动更新。

## 导入与原文读取

调用方只上传一份已确认 Canonical TXT；服务保存这份上传内容，无法核验上传前是否被改写。具体格式和边界见 [Spec 02](specs/02-小说导入与原文读取.md)。示例：

```text
书名：接口演示作品
标题：序章
　　第一自然段。
第二自然段。
```

UTF-8，可带 BOM；每条正文非空物理行是一个已确认自然段。书名与标题使用行首保留标记。服务不修正文、不猜标题、不合并硬换行；下载保留上传的全部字节。

以下 Python 示例作为 HTTP 调用演示，在启动服务后通过项目环境运行；不是独立业务 CLI：

```python
from uuid import uuid4
import httpx

data = "书名：接口演示作品\n标题：序章\n　　第一自然段。\n第二自然段。".encode("utf-8")
request_id = str(uuid4())  # 保存该键；超时重试时不要换键。
with httpx.Client(base_url="http://127.0.0.1:8000", timeout=120, trust_env=False) as client:
    check = client.post("/work-imports/validate", files={"file": ("demo.txt", data)})
    check.raise_for_status()
    if check.json()["status"] != "valid":
        raise RuntimeError(check.json())
    response = client.post(
        "/work-imports", files={"file": ("demo.txt", data)}, data={"request_id": request_id}
    )
    response.raise_for_status()
    work_id = response.json()["work"]["id"]
    directory = client.get(f"/works/{work_id}/sections").json()
    section_id = directory["items"][0]["id"]
    paragraphs = client.get(f"/works/{work_id}/sections/{section_id}/paragraphs").json()
    print(paragraphs)
    assert client.get(f"/works/{work_id}/file").content == data
```

完整接口描述在 [本机 OpenAPI 页面](http://127.0.0.1:8000/docs)，实际接口路径与错误码见 Spec 02。相同 request_id 和字节重试返回原作品；相同请求键换内容或用新键重复同名作品均返回 409。首次导入 201、重放 200。结果丢失可查询 `GET /work-imports/{request_id}`；404 只表示当时尚无已提交结果。

目录和正文分页默认 100、最大 1000。范围读取与补读必须提供同作品、同 Section 的段落 ID；返回完整自然段和实际范围。未提供文学分析或“已分析”进度。

## 本地 MCP 使用

准备一份结构已确认的 TXT，按上文配置数据库后启动应用即可，无需配置导入目录：

```powershell
uv run --locked python -m novel_lens
```

在支持 Streamable HTTP 的 AI 客户端中添加 MCP 服务，URL 填写 `http://127.0.0.1:8000/mcp`；更改应用端口时同步修改 URL。客户端无需数据库密码或 Python 子进程命令。不同客户端的配置字段由该客户端规定，本项目不自动修改客户端设置。

当前共提供 18 个工具，其中九个原文工具如下，另外九个分析资产工具见下一节：

| 用途 | 工具 |
| --- | --- |
| 文件预检、导入和结果恢复 | `work_import_validate`、`work_import`、`work_import_get` |
| 作品列表和元数据 | `work_list`、`work_get` |
| 目录、段落、范围和补读 | `source_sections`、`source_paragraphs`、`source_read`、`source_get_context` |

例如，文件位于 `D:/小说/样例.txt` 时，预检参数为 `{"file_path":"D:/小说/样例.txt"}`。正式导入使用同一路径并增加调用方生成的 UUID `request_id`。超时或丢失响应时先查询该键，再用原键和原文件重试。预检不锁定文件，导入时会重新读取和校验。

允许读取任意目录中的普通文件，使用服务进程现有的操作系统权限。推荐绝对路径；相对路径以服务进程工作目录为基准，支持 `..` 和指向其他目录的符号链接或目录联接。路径必须能被应用所在的操作系统识别。文件可读性、大小、读取期间变化和 TXT 格式仍会校验。入库后原文保存在 PostgreSQL，不再依赖本地路径。

每次工具结果的业务 JSON 上限为 1 MiB，另含同内容的兼容文本副本及协议开销。结果过大返回 `RESULT_TOO_LARGE`，可缩小分页或读取范围；单段仍超限时，本期 MCP 无法返回该完整段落。整本下载使用同服务的 REST 文件接口。完整参数、错误和取消恢复规则见 [Spec 03](specs/03-本地MCP原文能力接入.md)。

协议使用官方 Python SDK 2.2.0，已通过官方客户端实测；实际 AI 客户端宿主尚未配置或联调。工具仅提供数据能力，不自动分析小说。

## 分析标注与标签

外部 AI 读取原文并判断值得保留的写法后，通过以下 MCP 工具保存分析成果。服务不生成文学判断，保存标注也不表示该段落或全书已分析完成。

| 用途 | 工具 |
| --- | --- |
| 查询标签名称、定义和别名 | `tag_search`、`tag_list`、`tag_get` |
| 创建共享标签 | `tag_create` |
| 保存和修订标注 | `annotation_create`、`annotation_update` |
| 查找摘要和读取完整标注 | `annotation_list`、`annotation_get` |
| 恢复某次已提交写入 | `asset_write_get` |

一条 Annotation 至少引用一个 SourceRange，可以引用同一作品的多个章节；单个范围不能跨 Section。tag_ids 可为空，note 可为 null，也可以是保留换行的多段写法说明。不同标注允许重叠。标签跨作品复用，创建前先搜索名称、定义与别名；本期搜索为字面子串匹配，不是语义检索。

`annotation_list` 必须指定作品，可组合原文范围相交和“同时具有全部指定标签”过滤；只返回候选摘要，note_preview 最多 200 个字符，并明确 note_truncated。选择后调用 `annotation_get` 获取完整说明、标签 ID 和原文引用，再用 `source_read` 复读原文。

三个写工具都要求保存 request_id。修改还需 expected_version，并完整提交 source_ranges、tag_ids、note；清空标签传 []，清空说明传 null。VERSION_CONFLICT 表示有其他修订已提交，需要先读取当前版本。超时或响应丢失后查询 `asset_write_get`，再使用原键和原输入重试；该工具返回当次提交的快照，不代表标注的最新版本。

单次完整写入结果上限为 1 MiB，提交前检查，超限回滚并返回 ASSET_TOO_LARGE；分页结果超限可缩小 limit。标注、关联和请求结果在一个事务中保存，失败不留下部分成果。本期资产入口仅为 MCP，已有 REST 原文接口继续可用。完整字段和错误规则见 [Spec 04](specs/04-分析标注与标签管理.md)。

## 验证

基础检查：

```text
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
```

未设置测试数据库时，PostgreSQL 和依赖数据库的真实 HTTP / MCP 测试明确跳过。需要完整验证时，先按上述方式设置开发连接，再执行：

```powershell
$env:NOVEL_LENS_TEST_DATABASE_URL = $env:NOVEL_LENS_DATABASE_URL
uv run --locked pytest
uv run --locked alembic check
```

测试账号需要建库权限。测试创建随机命名的独立数据库，执行 Alembic 迁移，结束后删除当次测试库；不会清空开发库，不读取或修改小说素材。测试库中的小样例覆盖原字节与坐标保真、分页、范围隔离、名称冲突、并发幂等、事务失败回滚、无 Content-Length 的请求上限及真实服务重启。

2026-09-18：Windows Python 3.12.13 / uv 0.11.28 上全量 56 项测试通过，无跳过；Ruff、格式与 mypy 检查通过。真实 HTTP 进程、官方 MCP 客户端与 PostgreSQL 18.6 验证全部 18 个工具、写入重放、原提交快照恢复、并发版本冲突、单快照读取、数据库故障回滚及容量限制。已有 0001 原文库升级至 0002 后字节与坐标不变，Alembic check 通过。本地开发库已升级至 0002，作品、标签和标注均为空；测试库和进程已清理。测试框架有一项 Starlette / AnyIO 上游弃用提示。

工程启动基础另已在 WSL Ubuntu 24.04 / Python 3.12.3 验证，含 HTTP、非法配置、端口占用、SIGINT 停止与端口释放；Windows 验证过 Ctrl+C，本次 MCP 集成通过 Ctrl+Break 验证正常关闭及端口释放。测试进程均已停止。此次未验证 WSL 应用的 MCP、实际 AI 客户端宿主、长篇导入吞吐或模型性能。

## 项目资料

- [需求与设计基线](docs/小说分析与创作知识服务_需求与设计基线.md)
- [分析指南](docs/小说作品深度分析与写法标注指南.md)
- [写作原则](docs/中文小说正文写作原则.md)
- [开发约定](AGENTS.md)

实现位于 src/novel_lens/，测试位于 tests/，迁移位于 migrations/，功能规格位于 specs/。小说素材保留在已忽略的 data/，不随代码上传。
