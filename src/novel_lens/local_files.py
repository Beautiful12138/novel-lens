"""按调用方提供的路径读取单份 TXT 字节；不写入、整理或覆盖本地素材。"""

import io
import os
import stat
from pathlib import Path

from novel_lens.errors import ServiceError


def read_source(file_path: str, maximum: int) -> bytes:
    """读取任意目录的普通文件，相对路径以服务进程工作目录为基准。

    使用进程现有的操作系统权限；有界读取及变化检查不提供与外部编辑器之间的原子快照。
    """
    try:
        target = Path(file_path).resolve(strict=True)
        if not stat.S_ISREG(target.stat().st_mode):
            raise ServiceError("FILE_UNREADABLE", "输入必须是可读的普通文件")
        with target.open("rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ServiceError("FILE_UNREADABLE", "输入必须是可读的普通文件")
            if before.st_size > maximum:
                raise ServiceError("FILE_TOO_LARGE", "文件超过允许大小", 413)
            with io.BytesIO() as output:
                while chunk := source.read(min(65536, maximum + 1 - output.tell())):
                    output.write(chunk)
                    if output.tell() > maximum:
                        raise ServiceError("FILE_TOO_LARGE", "文件超过允许大小", 413)
                after = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ) or output.tell() != after.st_size:
                    raise ServiceError(
                        "SOURCE_FILE_CHANGED", "读取期间文件发生变化，请稳定文件后重试"
                    )
                return output.getvalue()
    except FileNotFoundError:
        raise ServiceError("FILE_NOT_FOUND", "指定文件或所在目录不存在") from None
    except (OSError, RuntimeError, ValueError):
        # 原始系统错误含完整路径，不作为工具错误或日志暴露。
        raise ServiceError("FILE_UNREADABLE", "无法读取指定文件") from None
