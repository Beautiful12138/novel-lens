"""验证项目配置契约，所有文件在独立临时目录中生成。"""

import os
from pathlib import Path

import pytest

from novel_lens.config import ConfigurationError, load_settings


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离开发者配置和工作目录，避免测试读取真实 .env 或素材。"""
    monkeypatch.chdir(tmp_path)
    for key in os.environ:
        if key.upper().startswith("NOVEL_LENS_"):
            monkeypatch.delenv(key)


def test_defaults_do_not_read_parent_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("NOVEL_LENS_PORT=9001\n", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    settings = load_settings()
    assert (settings.host, settings.port, settings.log_level) == ("127.0.0.1", 8000, "INFO")
    assert settings.mcp_profile == "business"


def test_environment_overrides_utf8_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(
        "# 开发配置\nNOVEL_LENS_PORT=9001\nNOVEL_LENS_LOG_LEVEL=debug\n", encoding="utf-8"
    )
    assert load_settings().port == 9001
    monkeypatch.setenv("NOVEL_LENS_PORT", "9002")
    monkeypatch.setenv("UNRELATED_SETTING", "ignored")
    settings = load_settings()
    assert settings.port == 9002
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("key", "value", "rule"),
    [
        ("NOVEL_LENS_PORT", "private-invalid-input", "1–65535"),
        ("NOVEL_LENS_PORT", "0", "1–65535"),
        ("NOVEL_LENS_PORT", "65536", "1–65535"),
        ("NOVEL_LENS_PORT", "", "1–65535"),
        ("NOVEL_LENS_HOST", "   ", "非空"),
        ("NOVEL_LENS_LOG_LEVEL", "private-invalid-input", "DEBUG"),
        ("NOVEL_LENS_DATABASE_URL", "private-invalid-input", "postgresql+psycopg"),
        ("NOVEL_LENS_DATABASE_URL", "sqlite:///local.db", "postgresql+psycopg"),
        ("NOVEL_LENS_MAX_FILE_BYTES", "0", "正整数"),
        ("NOVEL_LENS_MAX_REQUEST_BYTES", "private-invalid-input", "正整数"),
        ("NOVEL_LENS_MCP_PROFILE", "private-invalid-input", "business 或 maintenance"),
    ],
)
def test_invalid_environment_fails_without_input(
    key: str, value: str, rule: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 高优先级输入错误不能回退到 .env 中的有效值。
    (tmp_path / ".env").write_text("NOVEL_LENS_PORT=9001\n", encoding="utf-8")
    monkeypatch.setenv(key, value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert key in str(caught.value)
    assert rule in str(caught.value)
    assert "private-invalid-input" not in str(caught.value)


def test_unknown_dotenv_key_fails_without_input(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("NOVEL_LENS_POTR=private-input\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "NOVEL_LENS_POTR" in str(caught.value)
    assert "未知配置项" in str(caught.value)
    assert "private-input" not in str(caught.value)


def test_invalid_utf8_fails_without_input(tmp_path: Path) -> None:
    (tmp_path / ".env").write_bytes(b"NOVEL_LENS_HOST=private-input\xff")
    with pytest.raises(ConfigurationError, match="UTF-8") as caught:
        load_settings()
    assert "private-input" not in str(caught.value)


def test_local_host_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    for address in ("127.0.0.1", "127.0.0.2", "::1", "localhost"):
        monkeypatch.setenv("NOVEL_LENS_HOST", address)
        assert load_settings().host == address
    for address in ("0.0.0.0", "::", "192.168.1.2", "example.com"):
        monkeypatch.setenv("NOVEL_LENS_HOST", address)
        with pytest.raises(ConfigurationError, match="回环"):
            load_settings()
