"""PostgreSQL 连接生命周期；创建引擎不建立连接，也不自动创建表。"""

from sqlalchemy import Engine, create_engine

from novel_lens.config import Settings
from novel_lens.errors import ServiceError


class Database:
    """每应用持有一个连接池，在实际业务操作时才连接数据库。"""

    def __init__(self, settings: Settings) -> None:
        self._engine: Engine | None = None
        if settings.database_url is not None:
            self._engine = create_engine(
                settings.database_url.get_secret_value(),
                pool_pre_ping=True,
                hide_parameters=True,
                connect_args={"connect_timeout": 5},
            )

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise ServiceError("DATABASE_UNAVAILABLE", "尚未配置数据库连接", 503)
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
