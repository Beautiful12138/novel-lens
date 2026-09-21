# NovelLens

小说分析与创作知识服务。保存可回读的小说原文、分析标注和任务进度，通过 HTTP / MCP 为外部 AI 提供数据与检索能力；文学判断由 AI 完成。

## 当前可以做什么

- 导入结构已确认的 TXT，保留上传原字节和稳定段落坐标。
- 保存分析标注、标签、人物等实体、跨章节关系和风格导航。
- 保存分析任务与进度，中断后恢复已保存的工作。
- 按作品搜索原文及写法说明中的关键词。
- 使用独立的小说分析、中文写作 Skill。

目前提供本机 HTTP / MCP 服务，没有 Web UI；语义检索尚未实现。已提供可选的独立 embedding 配置验证与启动入口，由 AI 按部署指南检查环境、选择参数，验证后保存复用。当前业务使用无需 GPU 或模型下载。服务没有账号鉴权，只允许回环监听。

## 快速开始：新建自己的实例

需要 Git、Python 3.12、uv，以及能够运行 Linux 容器的 Docker 和 Docker Compose。Windows 可以使用 Docker Desktop，或在 WSL 中安装并运行这些工具；在同一个环境中执行整套命令，不混用 Windows / WSL 虚拟环境。

```text
git clone https://github.com/Beautiful12138/novel-lens.git
cd novel-lens
uv sync --locked
uv run --locked python scripts/init_local.py
docker compose up -d --build --wait
uv run --locked alembic upgrade head
uv run --locked python -m novel_lens
```

Windows PowerShell 执行 Python 命令前设置 `$env:PYTHONUTF8 = '1'`。首次构建数据库镜像需要联网下载公开的基础镜像和软件包，可能耗时数分钟。初始化脚本生成随机密码、`.env` 和密码文件，遇到已有配置会停止，不覆盖文件。已有实例不要重新初始化。

默认应用地址为 `http://127.0.0.1:8000`，数据库只发布本机 `15432` 端口。浏览器访问：

- [存活检查](http://127.0.0.1:8000/health)：应返回 `{"status":"ok"}`。
- [作品列表](http://127.0.0.1:8000/works)：新库应返回空列表响应；这一步还验证应用能读取数据库。
- [HTTP 接口文档](http://127.0.0.1:8000/docs)：可调用导入及查询接口。

`/health` 成功只表示应用进程可响应，不能代替数据库或 MCP 验证。首次导入、MCP 接入、端口冲突、已有数据库及旧部署升级见[部署指南](docs/部署指南.md)。所有数据保存在用户自己的环境，不需要连接维护者的服务器。

## 连接 AI 与 Skill

需要 AI 协助准备本机 embedding 时，将[部署指南中的任务说明](docs/部署指南.md#ai-协助准备-embedding)交给 AI。该入口独立于下述业务 Skill，不需要历史会话或维护者的机器配置。

在支持 Streamable HTTP 的 MCP 客户端中添加 `http://127.0.0.1:8000/mcp`。具体配置字段取决于客户端；连接后应能发现工具并调用 `work_list`。此地址是 MCP 协议入口，不是浏览器页面。

将以下完整目录提供给支持 Skill 的 AI 宿主，按宿主规则安装或加载：

- [novel-analysis](.agents/skills/novel-analysis/SKILL.md)：小说深读、写法标注与分析任务接续。
- [chinese-novel-writing](.agents/skills/chinese-novel-writing/SKILL.md)：构思、正文创作与修订；需要参考库时调用 NovelLens。

交付单位是完整 Skill 目录，包含 `SKILL.md` 与 `references/`。普通中文写作不强制连接服务。工具示例、TXT 格式和业务接口见[接口与使用](docs/接口与使用.md)。

## 数据保存与停止

默认数据库保存在 Docker 命名卷 `novel-lens-local_postgres-data`，包括已导入的原文件、章节、段落、分析资产和进度。工作区 `data/` 中尚未导入的文件仍是本机文件，不会自动写进数据库；`.env` 和 `data/postgres/password` 是本机配置，均不进入 Git。

Ctrl+C 停止 NovelLens；`docker compose stop` 停止数据库，`docker compose start --wait` 重新启动。之后运行 `uv run --locked python -m novel_lens` 即可，无需重复初始化。`docker compose down` 保留命名卷，`down -v` 会删除数据库数据。

更换电脑时，拉取代码不会带走数据。可以迁移自己的数据库备份，或配置连接同一个已有数据库；见部署指南。

## 验证

```text
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

数据库集成测试需要在当前终端设置 `NOVEL_LENS_TEST_DATABASE_URL`，指向具备 PGroonga 的测试实例；账号须能创建测试数据库和扩展。未设置时这些测试明确跳过，不能视为完整通过。测试创建并删除随机独立库，不清空业务库。Windows 测试前设置 `PYTHONUTF8=1`。

公开部署路径的验证范围见[部署指南](docs/部署指南.md#验证范围)，各功能历史验证记录见对应 Spec。使用者或协助操作的 AI 可按[本地环境模板](docs/本地环境.example.md)保存实机检查结果与实际服务入口，供后续会话接续；这是可选记录，不是首次部署的前置要求，也不是程序读取的配置文件。

- [需求与设计基线](docs/小说分析与创作知识服务_需求与设计基线.md)
- [功能规格](specs/)
- [语义检索与 embedding 规格](specs/10-原文语义检索与Embedding部署.md)：独立运行入口已实现，语义索引与接口仍待实现。
- [Embedding 候选与 CPU 验证](docs/Embedding选型与CPU验证.md)：实测结果与开发者实验命令，不是现行部署要求。
- [开发约定](AGENTS.md)
