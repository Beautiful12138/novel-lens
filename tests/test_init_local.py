"""新用户配置初始化的文件行为，不连接数据库或使用开发者凭据。"""

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
initialize = cast(
    Callable[[Path], None], runpy.run_path(str(ROOT / "scripts/init_local.py"))["initialize"]
)


def test_initialize_and_repeat_preserves_credentials(tmp_path: Path) -> None:
    initialize(tmp_path)
    password = (tmp_path / "data/postgres/password").read_text().strip()
    config = (tmp_path / ".env").read_bytes()
    assert len(password) == 64
    assert f":{password}@127.0.0.1:15432/novel_lens".encode() in config
    with pytest.raises(FileExistsError):
        initialize(tmp_path)
    assert (tmp_path / ".env").read_bytes() == config
    assert (tmp_path / "data/postgres/password").read_text().strip() == password


@pytest.mark.parametrize("existing", [".env", "data/postgres/password"])
def test_existing_partial_configuration_is_not_overwritten(tmp_path: Path, existing: str) -> None:
    path = tmp_path / existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("existing user configuration")
    with pytest.raises(FileExistsError):
        initialize(tmp_path)
    assert path.read_text() == "existing user configuration"
    other = "data/postgres/password" if existing == ".env" else ".env"
    assert not (tmp_path / other).exists()


def test_failed_env_write_removes_only_new_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.open

    def fail_env(path: Path, *args: object, **kwargs: object) -> object:
        if path == tmp_path / ".env":
            raise PermissionError("test write failure")
        return original(path, *args, **kwargs)  # type: ignore[call-overload]

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_env)
        with pytest.raises(PermissionError):
            initialize(tmp_path)
    assert not (tmp_path / "data/postgres/password").exists()
    assert not (tmp_path / ".env").exists()
    initialize(tmp_path)
    assert (tmp_path / ".env").is_file()
