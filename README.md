# NovelLens

小说分析与创作知识服务。外部 AI 负责文学判断，服务负责原文、分析资产和检索等稳定数据能力。

已实现 [工程基础](specs/01-工程基础与运行约定.md)、[小说导入与原文读取](specs/02-小说导入与原文读取.md)、[本地 MCP 接入](specs/03-本地MCP原文能力接入.md)、[分析标注与标签管理](specs/04-分析标注与标签管理.md)、[实体与跨章节关系](specs/05-实体与跨章节关系.md)、[轻量风格导航](specs/06-轻量风格导航.md)、[分析进度与中断接续](specs/07-分析进度与中断接续.md)和[原文与写法说明全文检索](specs/09-原文与写法说明全文检索.md)：接收一份结构已确认的 TXT，保留原字节和固定坐标，支持带原文引用的写法说明、共享标签、作品内实体、多节点关系、风格导航、任务进度、原子 checkpoint、中断接续及作品内关键词搜索。语义检索、模型、Web 和业务 CLI 尚未实现。

FastAPI 应用同时提供 REST 接口与 `/mcp`，共用一个进程、监听端口、业务层和数据库连接池。MCP 客户端通过本机 URL 连接；应用使用统一启动命令。本期只支持本地使用。

共享开发库已于 2026-09-21 升级至 0006，运行 PostgreSQL 18.6 与 PGroonga 4.0.8；原文及写法说明全文检索已启用，分析任务与中断接续继续可用。

分析任务支持指定范围的完整深读，任务进度独立、作品资产共享；继续分析恢复原任务，重新开展独立分析则新建任务。后续检索与效果验证范围按需求基线逐步确定。

## 独立 Skill

已按 [Spec 08](specs/08-分析与创作Skill.md) 提供两份 Skill：

- [novel-analysis](.agents/skills/novel-analysis/SKILL.md)：分析与标注标准、资产选择，以及 MCP 保存和任务接续。依靠 LLM 自身分析能力，完整分析指南留作维护参考，不随包加载。
- [chinese-novel-writing](.agents/skills/chinese-novel-writing/SKILL.md)：中文小说构思、正文创作、续写与修订；普通写作无需 MCP，需要 NovelLens 参考库时再读取包内查询流程。

交付单位是每个完整 Skill 文件夹（SKILL.md 与 references/）。两个包不依赖开发仓库、其他 Skill 或历史会话。涉及服务操作时，宿主须已连接 MCP，并使用实际发现的工具名；缺少连接不影响无需参考库的普通写作。

写作原则只随中文写作 Skill 携带一份。维护源为 [中文小说正文写作原则](docs/中文小说正文写作原则.md)，通过 [同步脚本](scripts/sync_skill_references.py) 更新副本；使用 Skill 的 AI 不需要原文档、脚本或 Python。入口与 MCP 工作流人工维护，分析指南不生成包内副本。

```powershell
uv run --locked python scripts/sync_skill_references.py
uv run --locked python scripts/sync_skill_references.py --check
```

宿主识别 Skill 后可使用以下任务表述；作品和范围应替换为实际目标：

```text
$novel-analysis 分析已导入作品《作品名》的第一章，保存分析资产并记录进度。
$novel-analysis 继续分析任务 <job_id>，先恢复进度和待核对问题。
$chinese-novel-writing 依据以下人物和前文续写重逢场景：……
$chinese-novel-writing 参考 NovelLens 中《作品名》的原文和分析，依据以下新作前文续写：……
```

参考查询支持风格导航、标签/实体字面查询、标注过滤、关系查询、原文与写法说明关键词搜索及按坐标读取原文；全文能力需要 PGroonga 和迁移 0006，向量与统一 Search 尚未提供。实际 AI 宿主触发、文学效果与读取成本仍待后续联调。未安装到全局目录或修改宿主配置。

## 安装与运行

每台电脑可将 [环境说明模板](docs/本地环境.example.md) 复制为 `docs/本地环境.local.md`，记录本机执行位置、工具入口、数据库和验证情况。本地说明已被 Git 忽略，由各台电脑分别维护；README 中的通用要求仍然适用，具体平台命令和历史验证结果不代表当前电脑已具备相同环境。`.env`、小说素材和数据库数据不随 Git 同步。

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

## 开发数据库（远程服务器）

开发数据库部署在 Linux 服务器，开发电脑运行 Python 应用，通过公网端口 `10002` 直连 PostgreSQL 18.6。本机不需要 Docker 或 WSL；HTTP 和 MCP 仍只监听本机。当前服务器地址与本机连接验证见各电脑的 `docs/本地环境.local.md`。

[compose.yaml](compose.yaml) 在服务器运行，固定本地构建镜像的内容 ID，禁止隐式拉取。[Dockerfile](docker/postgres/Dockerfile) 在原官方 PostgreSQL 18.6 镜像上安装 PGroonga 4.0.8，不升级或降级 PostgreSQL。部署目录为 `/opt/novel-lens`，容器为 `novel-lens-postgres`，数据库与初始化管理账号为 `novel_lens`，命名卷为 `novel-lens-postgres-data`。此实例独立于服务器其他项目的数据库。

### 连接配置

数据库关闭 TLS，使用 SCRAM 密码认证；连接数据不加密。每台电脑分别准备被 Git 忽略的 `.env`，不通过 Git 同步密码：

```text
NOVEL_LENS_DATABASE_URL=postgresql+psycopg://novel_lens:<URL编码的密码>@<数据库地址>:10002/novel_lens?sslmode=disable
```

客户端使用 `sslmode=disable`，无需证书。Navicat 中关闭 SSL，直接填写数据库地址、端口、账号和密码。

目标实例需先具备 PGroonga 扩展文件，应用不会安装扩展软件包或自动迁移。当前共享库已完成 0006，无需重复升级；新建环境使用已验证镜像后显式执行迁移。

```text
uv run --locked alembic upgrade 0006
uv run --locked python -m novel_lens
```

应用不会自动建表或迁移；连接未配置时 REST 业务请求返回 503，MCP 返回 DATABASE_UNAVAILABLE。迁移 0001 建立原文相关表，0002 新增分析标注、标签与写入请求表，0003 新增实体、标注实体关联、关系节点和关联表，0004 新增风格导航、条目和证据范围三张表，0005 新增分析任务、固定目标范围和稀疏段落进度三张表；既有原文和历史请求保留。0006 启用 PGroonga 并直接索引 paragraphs.text 与 annotations.note。已有 0005 库可继续使用原功能，搜索返回 SEARCH_UNAVAILABLE（REST 503）；工具枚举不证明全文已启用。共享开发库当前为 0006，未安装 pgvector。

共享镜像包含 PostgreSQL 18.6、PGroonga 4.0.8 和 Groonga 16.1.0，已完成备份恢复及正常重启后的搜索验证。数据库切换保留原命名卷、实例标识、端口与认证配置。PGroonga 的关键词匹配与中文分词配置见 Spec 09。

多台电脑连接同一远程库时共享作品与分析数据。执行迁移前需确认代码版本与目标数据库，避免多个不同版本的客户端各自变更共享库结构。测试创建独立随机数据库，不清空共享开发库。

### 服务器维护

从服务器 `/opt/novel-lens` 执行：

```sh
docker compose ps
docker compose stop postgres
docker compose start postgres
```

首次部署需先用 `docker load -i <已验证镜像归档>` 加载 compose.yaml 指定 ID 的镜像，准备以下服务器本地文件，再执行 `docker compose up -d --pull never --wait postgres`。镜像归档及校验和保留在升级备份目录，具体位置见本地环境说明：

- `data/postgres/password`：初始化管理账号的密码，不提交 Git。
- `data/postgres/pg_hba.conf`：使用以下规则，允许容器内 Unix socket 维护，网络连接要求 SCRAM 密码认证：

```text
local all all trust
host all all all scram-sha-256
```

HBA 文件需对容器内 PostgreSQL 用户可读。数据库文件保存在命名卷，挂载于容器 `/var/lib/postgresql`；完整原文存储在 `work_sources.content`，不会自动复制回本地素材目录。`docker compose down` 保留命名卷，`down -v` 会删除数据库数据。已有数据卷的密码不会随密码文件变化自动更新，须显式修改数据库角色并同步客户端配置。

重建镜像使用 `docker build -t novel-lens-postgres:18.6-pgroonga4.0.8-20260921 docker/postgres`。Dockerfile 固定基础镜像、扩展版本及仓库引导包校验和，但仓库内传递依赖可能变化，因此重建产物不保证具有相同 ID。核对版本并完成隔离恢复和搜索验证后，再更新 compose.yaml 中的镜像 ID；需要复用相同产物时直接加载已保存归档。

每次升级前保存 pg_dump 与旧 compose.yaml。回退需先核对迁移和扩展状态，不能仅换回不含 PGroonga 的旧镜像。0006 回退只删除两项索引、保留扩展；恢复升级前快照须在独立库验证并明确数据覆盖范围。当前没有启用实验性的自动崩溃恢复配置；异常停机后如索引不可用，应核对原表并重建两项 PGroonga 索引。正常重启与索引重建已验证，强制断电场景未验证。扩展的恢复机制见 [PGroonga 官方说明](https://pgroonga.github.io/reference/crash-safe.html)。

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

当前共提供 42 个工具，其中九个原文工具如下，九个标注与标签工具、十一个实体与关系工具、三个风格导航工具、八个任务与进度工具和两个全文搜索工具见后续章节：

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

一条 Annotation 至少引用一个 SourceRange，可以引用同一作品的多个章节；单个范围不能跨 Section。tag_ids、entity_ids 可为空，实体必须属于该作品；note 可为 null，也可以是保留换行的多段写法说明。不同标注允许重叠。标签跨作品复用，创建前先搜索名称、定义与别名；本期搜索为字面子串匹配，不是语义检索。

`annotation_list` 必须指定作品，可组合原文范围相交、“同时具有全部指定标签”和“同时具有全部指定实体”过滤；只返回候选摘要及 entity_count，note_preview 最多 200 个字符，并明确 note_truncated。选择后调用 `annotation_get` 获取完整说明、标签与实体 ID 和原文引用，再用 `source_read` 复读原文。

三个写工具都要求保存 request_id。修改还需 expected_version，并完整提交 source_ranges、tag_ids、note；清空标签传 []，清空说明传 null。VERSION_CONFLICT 表示有其他修订已提交，需要先读取当前版本。超时或响应丢失后查询 `asset_write_get`，再使用原键和原输入重试；该工具返回当次提交的快照，不代表标注的最新版本。

修改标注时，entity_ids 省略保留当前关联，显式 [] 才清空。升级前的成功请求、快照及无实体过滤的游标继续可用；历史快照可能不含 entity_ids，不能据此判断当前没有实体关联。

单次完整写入结果上限为 1 MiB，提交前检查，超限回滚并返回 ASSET_TOO_LARGE；分页结果超限可缩小 limit。标注、关联和请求结果在一个事务中保存，失败不留下部分成果。本期资产入口仅为 MCP，已有 REST 原文接口继续可用。完整字段和错误规则见 [Spec 04](specs/04-分析标注与标签管理.md)。

## 实体与跨章节关系

Entity 是作品内的身份入口，支持人物、地点、物品、组织、概念五类。名称和别名允许重复，以稳定 ID 区分；创建前先搜索并判断是否复用。修改实体名称不改变已有标注或关系的版本。

| 用途 | 工具 |
| --- | --- |
| 创建、修改、读取实体 | `entity_create`、`entity_update`、`entity_get` |
| 列出、搜索实体 | `entity_list`、`entity_search` |
| 创建和修改关系 | `relation_create`、`relation_update` |
| 查询说明和节点概览 | `relation_search`、`relation_get`、`relation_expand` |
| 撤回或恢复关系 | `relation_set_status` |

Relation 至少连接同作品的两个不同原文范围，可跨章节，允许直接引用尚未标注的原文。节点保留调用方给出的顺序及 role；title、relation_type 和关系 note 必填。类型和角色使用开放文本，文学依据由外部 AI 核验，服务不自动判断伏笔或完成分析。

典型读取流程：`entity_search` 找到对象 → `annotation_list` 或 `relation_search` 按 entity_ids 找到候选 → `relation_get` 读取完整说明和版本 → `relation_expand` 携带 expected_version 分页获取节点角色与范围 → `source_read` 读取选中的证据。关系详情不附带节点列表，节点概览不附带正文；翻页期间版本变化返回 VERSION_CONFLICT。

关系创建时 status=active。`relation_set_status` 要求 request_id、work_id、relation_id、expected_version、status；传 withdrawn 撤回，传 active 恢复，保留全部内容和引用。普通关系查询默认排除已撤回记录，显式 status=withdrawn 可查询撤回记录，status=null 查询全部；按 ID 获取或展开仍可查看已撤回关系。普通内容修改保留当前状态，不自动恢复。

五个实体和关系写工具共用 `asset_write_get`、请求键空间、版本检查和事务恢复。关系内容修改须完整提交标题、类型、说明、节点、标签和实体集合。状态与内容修改竞争同一版本；每个成功的新修改请求增加版本，同键重放保持原结果。写入快照包含全部节点，但不含正文；它仅代表当次提交，不代表当前关系仍有效。完整参数和验收条件见 Spec 05。

## 轻量风格导航

每部作品最多一份 StyleGuide，按 work_id 定位。scope_note 必须说明实际分析范围和局限；entries 保存有序的写法条目，每项包含 title、kind、description、applicability、source_ranges。kind 为 baseline（常态）、variation（条件性变化）或 exception（例外）；常态也仅在声明的范围内成立。说明为自由文本，服务不规定文学指标。

| 用途 | 工具 |
| --- | --- |
| 首次创建导航 | `style_guide_create` |
| 获取当前完整导航 | `style_guide_get` |
| 按版本整体修订 | `style_guide_update` |

创建传 request_id、work_id、scope_note 和 entries；每个条目至少引用一个同作品 SourceRange，条目内不允许完全重复引用，不同条目可共享证据。读取只返回说明和证据位置，选择适用条目后用 `source_read` 核验正文。第一版不关联 Annotation 或 Relation。

更新除创建字段外还须提供 expected_version，完整替换范围说明和全部条目，保留仍成立的判断；entries=[] 撤除全部结论，不删除导航主记录。版本冲突须重新读取后再修订，不自动合并。作品不存在返回 WORK_NOT_FOUND，尚无导航返回 STYLE_GUIDE_NOT_FOUND，新请求重复创建返回 STYLE_GUIDE_EXISTS。

两个写工具复用 `asset_write_get`、共享请求键、事务和 1 MiB 完整写入结果上限。超时先查原键，再以原键原输入重试；重排条目或证据也属于不同输入。恢复结果是原提交快照，当前导航用 `style_guide_get` 读取。失败不留下部分条目或错误推进版本。

scope_note 是调用方的范围声明，服务不核验是否已充分分析；单独保存导航不推进 Coverage 或 AnalysisJob，不代表全书完成。文学判断及其证据充分性由外部 AI 核验。完整契约和验收条件见 [Spec 06](specs/06-轻量风格导航.md)。

## 原文与写法说明全文检索

REST 使用 `POST /source/search` 和 `POST /annotations/search`；MCP 对应 `source_search`、`annotation_search`，请求体相同：

```json
{"work_id": "<实际作品 UUID>", "terms": ["月光", "张三"], "match": "all", "limit": 20}
```

terms 为 1–8 个普通关键词，每词最多 128 字符；去除首尾空白、去重，拒绝空白词与 NUL，不解释查询表达式。all 要求同一自然段或同一条写法说明包含所有词，any 则任一词命中。中文支持单字与连续词；ASCII 按词且忽略大小写，兼容字符经归一化处理，因此 `ABC` 可命中 `ＡＢＣ`，`bc` 不命中 `abcd`。这不是同义词或语义搜索。

搜索分页默认 20、最大 100；原文按章节和段落顺序，标注按创建时间和 ID 排序。续页传 next_cursor，保持 work_id、terms、match 和入口；说明修订可能改变后续页命中集合。结果用 match_kind 区分原文与说明，excerpt 最多 200 个原始字符，含 character_start、前后截断标记及 match_located；无法定位关键词时返回段首摘要并标记 false。摘要位置仅用于摘录，不作为精确的分词高亮坐标。

原文候选返回真实单段 source_range、paragraph_id、section_id，可用 source_read 或 source_get_context 回读；标注候选返回 annotation_id、version、first_source_range 和 source_range_count，再用 annotation_get 读取完整说明及全部证据。无标注原文也可命中。两入口均只读，不推进分析进度。完整约束见 [Spec 09](specs/09-原文与写法说明全文检索.md)。

## 分析进度与中断接续

每部作品可创建多个 AnalysisJob，target 选 whole_work 或同作品的 SourceRange 列表，创建后固定。任务之间的 Coverage 和 recovery 独立，Annotation、Entity、Relation 和 StyleGuide 仍为作品共享资产。新任务从 unprocessed 开始，不按现有标注数量推断进度。

| 用途 | 工具 |
| --- | --- |
| 创建、查找与读取任务 | `analysis_job_create`、`analysis_job_list`、`analysis_job_get` |
| 暂停、恢复、更新接续信息、重开 | `analysis_job_update` |
| 读取进度或显式标记已读、待回看 | `coverage_get`、`coverage_mark` |
| 原子保存本批成果、接续信息和 processed 进度 | `analysis_checkpoint` |
| 核验进度与导航版本后完成任务 | `analysis_job_complete` |

recovery 区分带原文证据的 facts、open_questions，以及必填 next_action 和可选 next_range；更新时整体替换。补读证据可在目标范围之外，但须属于同作品。读取原文没有进度副作用；read 不降级已处理状态，needs_revisit 须说明原因且不能包含未处理段落。

checkpoint 传 request_id、work_id、job_id、expected_version、一个目标内 source_range、writes 和完整 recovery。writes 的每项为 `{operation, input}`，支持现有十个资产写操作并各自保留 request_id。各键互不重复，不提供批内临时 ID；需引用新标签或实体时先创建并取得稳定 ID。writes=[] 须提供 outcome_note，可以说明“核对后没有新增结论”。任何子写入、接续信息、进度或提交失败都会回滚本批新内容，已在此前提交的资产不受影响。

所有任务修改使用版本条件；暂停后不能推进。目标全部 processed，且已提交当前 style_guide_version、calibration_note、limitations（可为 null）和最终 recovery 后才能完成；允许保留未决问题。completed 任务须以 running、reopen_reason 和完整 recovery 显式重开，保留进度并清除当前完成说明。

超时或更换会话后，先用 `asset_write_get` 查外层请求键，再读取当前任务、`coverage_get` 和 recovery，按需核验原文。历史回执不代替当前状态；未查到结果时以原键原输入重试。进度按相邻同状态、同原因合并并分页，任务版本变化后旧游标返回 VERSION_CONFLICT。完整参数、状态转换和错误见 [Spec 07](specs/07-分析进度与中断接续.md)。

## 验证

2026-09-21（共享库启用全文搜索）：PostgreSQL 保持 18.6，添加 PGroonga 4.0.8 / Groonga 16.1.0；镜像内容 ID 固定于 compose.yaml。升级前 pg_dump 已在隔离实例完成恢复和正常重启搜索验证；共享库从 0005 升至 0006，原数据卷、实例标识、认证配置及 21 张业务表内容摘要一致，两项搜索索引有效，Alembic check 无差异。新环境的随机独立库通过 19 项搜索专项测试；共享库通过 42 个 MCP 工具与只读索引查询验证，未写入测试素材。备份、旧配置及镜像归档已保留，临时验证进程与测试库已清理。实际 AI 宿主和文学效果尚未联调，本次未修改业务代码或提交推送 Git。

2026-09-21（Spec 09）：隔离 PostgreSQL 18.4 / PGroonga 4.0.8 上分批完成全部 126 项测试，无跳过；新增 19 项覆盖关键词语义、分页、摘要坐标、说明修订、索引/顺序扫描一致性、HTTP / MCP 对照和升级回退。Ruff、格式、严格 mypy、两份 Skill 格式校验及写作原则同步检查通过。0005 → 0006 保留原有数据，Alembic check 无差异；另完成 21 张业务表的 pg_dump / pg_restore 恢复、搜索结果对照与 REINDEX，以及无扩展实例的迁移失败回滚验证。隔离容器及测试库已清理，共享库未变更。实际 AI 宿主、全书规模性能和文学效果未测，保留既有 Starlette / AnyIO 弃用提示。

2026-09-20（Spec 08）：两份 Skill 格式校验通过，分别置于仓库外独立目录后，入口可达全部 5 个文件，3 处包内链接有效。单份写作原则同步的缺失、重复执行、源文档变化、副本误改与修复验证通过；维护脚本 Ruff、格式、严格 mypy 与 git diff --check 通过。7 个 MCP 请求模板与调整前一致，沿用此前独立 PostgreSQL 18.6 测试库中 26 次真实调用的证据，本次未访问数据库。临时目录已清理；实际 AI 宿主与文学效果未测。

2026-09-20（共享开发库升级至 0005）：已执行 0004 → 0005 迁移，Alembic check 无结构差异。18 张既有业务表的行数及内容摘要一致（升级前均为空），三张新增表为空。升级前已保留服务器 pg_dump 备份，并通过 pg_restore 归档读取检查；未执行备份恢复演练。临时本地应用通过健康检查、官方 MCP 客户端 40 个工具枚举、空作品列表及任务和进度查询的缺失资源错误验证，未向共享库写入测试数据。验证进程正常停止并释放端口，未修改实际 AI 宿主配置，未重新运行已通过的 107 项独立测试。

2026-09-20（Spec 07 开发阶段）：Windows Python 3.12.13、MCP SDK / Client 2.2.0、真实 PostgreSQL 18.6 上分批完成全部 107 项测试，无跳过；Ruff、格式、mypy、git diff --check 通过。覆盖任务隔离、范围规范化、分页版本、原子 checkpoint、提交失败回滚、并发状态竞争、官方 MCP 客户端及服务重启恢复。0003 / 0004 → 0005 升级保留既有业务表及历史快照，独立测试库 Alembic check 无差异；测试库已清理。共享开发库只读确认仍为 0004，未执行升级；实际 AI 客户端宿主、全书规模性能及文学效果尚未验证。具体批次见 Spec 07 第 11 节。

2026-09-20（共享开发库升级至 0004）：对已确认的共享开发库执行 0003 → 0004 迁移，Alembic check 无结构差异。15 张原有业务表的行数及内容摘要保持一致（升级前均为空），三张新增表为空。临时启动本地应用，验证 `/health`、官方 MCP 客户端枚举 32 个工具、空作品列表，以及 `style_guide_get` 对不存在作品返回 WORK_NOT_FOUND；未向共享库写入测试样例，临时进程正常停止并释放端口。导航写入、并发与回滚沿用独立测试库验证，未重复运行全量测试。

2026-09-20（Spec 06 开发阶段）：Windows Python 3.12.13、MCP SDK / Client 2.2.0、远程 PostgreSQL 18.6 上分批完成全部 90 个测试用例验证，无跳过。新增 15 项验证风格导航、三个 MCP 工具、并发与回滚、重启恢复及 0003 → 0004 迁移；原有 75 项完成回归。回归首轮中既有 1 MiB 原文下载测试发生一次 10 秒 ReadTimeout，原代码和原超时设置下单独复测通过，首次慢响应原因未定位。Ruff、格式、mypy、git diff --check 和独立测试库 Alembic check 通过；保留既有 Starlette / AnyIO 弃用提示。开发阶段共享库只读确认仍为 0003，未执行升级；实际 AI 客户端宿主和文学效果尚未验证。具体分批结果见 Spec 06 第 6 节。

2026-09-19（共享开发库升级）：对已确认的共享开发库执行 0002 → 0003 迁移，Alembic check 无结构差异。升级前后九张原有业务表的行数及内容摘要一致；新增六张表均为空，迁移未写入测试素材。此次仅执行迁移及结构、数据核验，未重跑下述已通过的全量测试，也未启动或重启 AI 客户端。

基础检查（Windows PowerShell 先在当前会话设置 `$env:PYTHONUTF8 = '1'`，确保应用子进程日志与测试读取使用 UTF-8）：

```text
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
```

未设置测试数据库时，PostgreSQL 和依赖数据库的真实 HTTP / MCP 测试明确跳过。需要完整验证时，先在 `.env` 配置并验证远程连接，再执行以下命令；测试连接只设置于当前会话：

```powershell
$env:NOVEL_LENS_TEST_DATABASE_URL = '<已安装 PGroonga 的独立测试实例连接，postgresql+psycopg://...>'
uv run --locked pytest
```

测试账号需要建库和创建 PGroonga 扩展的权限，实例须已安装扩展文件。不要将尚未安装扩展的共享实例作为新版全量测试目标。测试创建随机命名的独立数据库，执行 Alembic 迁移，结束后删除当次测试库；不会清空开发库，不读取或修改小说素材。测试覆盖原字节与坐标保真、分页、作品隔离、并发幂等、事务失败回滚、真实服务重启及全文语义。迁移测试在独立库执行 Alembic check；共享库仍在 0005 时相对新 metadata 存在两个待建搜索索引，不能将其视为已升级。

2026-09-19（Spec 05）：Windows Python 3.12.14、MCP SDK / Client 2.2.0、远程 PostgreSQL 18.6（sslmode=disable）上全量 75 项测试通过，无跳过；Ruff、格式、mypy 及 git diff --check 通过。真实进程覆盖全部 29 个 MCP 工具、实体与关系、撤回恢复、并发版本竞争、故障回滚及重启后原快照恢复。真实旧版代码生成的 0002 数据升级至 0003 后，原文、标注、标签、指纹、历史快照和旧游标兼容，Alembic check 无差异。所有迁移在独立测试库执行并由框架清理；共享开发库仅做只读核实，仍为 0002，作品和标注为 0。实际 AI 客户端宿主和全书性能尚未验证；有一项既有 Starlette / AnyIO 弃用提示。

2026-09-19（连接方式调整）：关闭服务器 TLS，客户端改为 `sslmode=disable`；公网直连成功，服务器 `ssl=off`、实际连接 `ssl=false`，错误密码被拒绝，迁移仍为 0002，作品、标签和标注均为 0。保留原数据卷；本次仅验证连接、认证和现有数据状态，未重跑此前通过的全量测试。

2026-09-19（初次部署，TLS 验证为当时状态）：远程 PostgreSQL 18.6 在 Linux 服务器部署完成，本机通过公网 10002 端口以 `verify-full` 校验证书并协商 TLS 1.3；非 TLS 网络连接被拒绝。迁移至 0002，Alembic check 通过。Windows Python 3.12.14 / uv 0.12.15 按锁文件安装完成；首轮测试 50 项通过、6 项因 Windows 子进程日志编码失败，当前会话设置 `PYTHONUTF8=1` 后重跑 6 项全部通过，合计 56 项验证通过，无跳过。临时测试库已清理，开发库作品、标签、标注均为 0；有一项既有 Starlette / AnyIO 弃用提示。本次未修改应用代码，未重复运行 Ruff 和 mypy。

2026-09-18：Windows Python 3.12.13 / uv 0.11.28 上全量 56 项测试通过，无跳过；Ruff、格式与 mypy 检查通过。真实 HTTP 进程、官方 MCP 客户端与 PostgreSQL 18.6 验证全部 18 个工具、写入重放、原提交快照恢复、并发版本冲突、单快照读取、数据库故障回滚及容量限制。已有 0001 原文库升级至 0002 后字节与坐标不变，Alembic check 通过。本地开发库已升级至 0002，作品、标签和标注均为空；测试库和进程已清理。测试框架有一项 Starlette / AnyIO 上游弃用提示。

工程启动基础另已在 WSL Ubuntu 24.04 / Python 3.12.3 验证，含 HTTP、非法配置、端口占用、SIGINT 停止与端口释放；Windows 验证过 Ctrl+C，本次 MCP 集成通过 Ctrl+Break 验证正常关闭及端口释放。测试进程均已停止。此次未验证 WSL 应用的 MCP、实际 AI 客户端宿主、长篇导入吞吐或模型性能。

## 项目资料

- [需求与设计基线](docs/小说分析与创作知识服务_需求与设计基线.md)
- [分析指南](docs/小说作品深度分析与写法标注指南.md)
- [写作原则](docs/中文小说正文写作原则.md)
- [开发约定](AGENTS.md)

实现位于 src/novel_lens/，测试位于 tests/，迁移位于 migrations/，功能规格位于 specs/。小说素材保留在已忽略的 data/，不随代码上传。
