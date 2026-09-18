"""读取当前工作目录配置，并生成不包含原始输入值的错误信息。"""

from ipaddress import ip_address
from typing import Annotated, Literal

from pydantic import Field, SecretStr, StringConstraints, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(ValueError):
    """可直接写入启动日志的配置错误，不携带配置输入值。"""


class Settings(BaseSettings):
    """环境变量优先于当前目录 .env，未设置的字段使用默认值。"""

    model_config = SettingsConfigDict(
        env_prefix="NOVEL_LENS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
    )

    host: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr | None = None
    max_file_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    max_request_bytes: int = Field(default=65 * 1024 * 1024, ge=1)

    @field_validator("host")
    @classmethod
    def validate_local_host(cls, value: str) -> str:
        """本地文件工具只随回环监听开放，不把本机访问边界扩大到网络。"""
        if value.lower() == "localhost":
            return "localhost"
        try:
            address = ip_address(value)
            if address.is_loopback:
                return str(address)
        except ValueError:
            pass
        raise ValueError("必须使用回环 IP 或 localhost")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        """仅接受本项目验证过的 PostgreSQL 驱动，校验过程不建立连接。"""
        if value is not None:
            try:
                url = make_url(value.get_secret_value())
                if url.drivername != "postgresql+psycopg" or not url.database:
                    raise ValueError("数据库连接格式无效")
            except ArgumentError:
                raise ValueError("数据库连接格式无效") from None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """允许人工配置使用小写日志级别，其他非法值仍由字段校验拒绝。"""
        return value.upper() if isinstance(value, str) else value


def load_settings() -> Settings:
    """在启动时加载配置；错误只暴露字段与规则，不回显原始值或异常链。"""
    try:
        return Settings()
    except ValidationError as exc:
        rules = {
            "host": "必须为非空的回环 IP 或 localhost",
            "port": "必须为 1–65535 的整数",
            "log_level": "必须为 DEBUG、INFO、WARNING、ERROR 或 CRITICAL",
            "database_url": "必须为包含数据库名的 postgresql+psycopg 连接串",
            "max_file_bytes": "必须为正整数",
            "max_request_bytes": "必须为正整数",
        }
        messages = []
        # Pydantic 的完整错误可能含输入值或自定义异常上下文，不能直接记入日志。
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            field = str(error["loc"][0])
            if field in rules:
                messages.append(f"NOVEL_LENS_{field.upper()}: {rules[field]}")
            else:
                messages.append(f"{field.upper()}: 未知配置项，请检查 .env 中的配置键")
        raise ConfigurationError("；".join(messages)) from None
    except (OSError, UnicodeError):
        raise ConfigurationError(".env: 无法读取，请检查文件权限与 UTF-8 编码") from None
