"""只为全新空库建立当前结构；已有库使用 Alembic 升级，不覆盖数据。"""

import sys
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from novel_lens.config import ConfigurationError, load_settings
from novel_lens.database import Database
from novel_lens.schema import metadata

ROOT = Path(__file__).resolve().parents[1]


def initialize(engine: Engine) -> str:
    """空库事务内创建当前表和 PGroonga 并登记版本；非空库或失败不留部分结构。

    直接使用当前元数据，避免新用户为运行已退役的历史迁移安装 pgvector。
    版本标记仅在全部结构创建成功后写入；不能用于跳过已有数据库的迁移。
    """
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_current_head()
    if revision is None:
        raise RuntimeError("没有可初始化的数据库版本")
    with engine.begin() as connection:
        # 排除扩展自有对象，拒绝用户已有表、视图、序列及 Alembic 版本记录。
        populated = connection.scalar(
            text("""
            SELECT EXISTS (
              SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S')
              AND NOT EXISTS (
                SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                AND d.objid=c.oid AND d.deptype='e'
              )
            )
        """)
        )
        if populated:
            raise ValueError("数据库非空；请先备份并按部署指南执行已有数据库升级")
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgroonga"))
        metadata.create_all(connection, checkfirst=False)
        MigrationContext.configure(connection).stamp(script, revision)
    return revision


def main() -> int:
    """错误输出不包含连接、数据库返回的正文或请求参数。"""
    database = None
    try:
        settings = load_settings()
        if settings.database_url is None:
            raise ValueError("未配置 NOVEL_LENS_DATABASE_URL")
        database = Database(settings)
        revision = initialize(database.engine)
    except (ConfigurationError, ValueError, RuntimeError) as exc:
        print(f"未初始化：{exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("初始化失败且事务已回滚；请检查连接、建表权限及 PGroonga 安装。", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.close()
    print(f"已建立当前数据库结构 {revision}；无需 embedding 或 vector 扩展。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
