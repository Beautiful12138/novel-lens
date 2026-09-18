"""命令行启动入口，仅运行 HTTP 服务，不承担业务 CLI 功能。"""

import logging
import sys

import uvicorn

from novel_lens.app import create_app
from novel_lens.config import ConfigurationError, load_settings


def main() -> int:
    """校验配置后启动服务；配置错误返回非零状态，绑定错误由 Uvicorn 报出。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    logger = logging.getLogger("novel_lens")
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        logger.error("配置错误：%s", exc)
        return 1

    logging.getLogger().setLevel(settings.log_level)
    # SDK 诊断可能包含整条协议消息或校验输入；项目适配层负责无输入的错误反馈。
    logging.getLogger("mcp").setLevel(logging.CRITICAL)
    logger.info("启动 NovelLens，绑定地址 %s:%s", settings.host, settings.port)
    # 沿用根日志处理器，避免 Uvicorn 的访问日志或默认 stdout 处理器输出查询内容。
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
