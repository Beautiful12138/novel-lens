"""显式执行迁移，连接配置复用应用规则，不在仓库保存数据库凭据。"""

from alembic import context

from novel_lens.config import load_settings
from novel_lens.database import Database
from novel_lens.schema import metadata

database = Database(load_settings())
try:
    with database.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
finally:
    database.close()
