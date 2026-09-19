"""用已提交的 0002 代码和独立 PostgreSQL 生成兼容样例，不调用新版资产代码。

从项目根目录用项目 Python 执行本脚本；连接读取本机 .env，仅创建并清理随机测试库。
输出 assets_0002.json 含虚构原文、真实旧指纹、快照和游标。更新样例必须重跑生成器。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import temporary_database  # noqa: E402

from novel_lens.config import load_settings  # noqa: E402

REVISION = "b060c4d"
CHILD = r"""
import json
import os
from pathlib import Path
from uuid import uuid4
from sqlalchemy import text
from novel_lens.asset_contracts import (
    TagCreate, AnnotationCreate, AnnotationUpdate, AnnotationList,
)
from novel_lens.assets import AssetService
from novel_lens.config import load_settings
from novel_lens.database import Database
from novel_lens.importing import ImportService
from novel_lens.reading import ReadingService
from novel_lens.contracts import SourceRange
from novel_lens.schema import metadata

database = Database(load_settings())
try:
    data = ("\ufeff书名：旧契约兼容样例\r\n标题：章一\r\n　原文😀 \t\r\n"
            "末段\n标题：章二\n尾声").encode()
    work = ImportService(database, 10000).import_source(data, uuid4()).work
    reading, assets = ReadingService(database), AssetService(database)
    ranges = []
    for section in reading.list_sections(work.id, 100, None).items:
        paragraphs = reading.list_paragraphs(work.id, section.id, 100, None).items
        ranges.append(SourceRange(work_id=work.id, section_id=section.id,
            start_paragraph_id=paragraphs[0].id, end_paragraph_id=paragraphs[-1].id))
    tag_request = TagCreate(request_id=uuid4(), namespace="机制", name="旧标签",
                            description="旧定义")
    tag = assets.create_tag(tag_request)
    create = AnnotationCreate(request_id=uuid4(), work_id=work.id, source_ranges=ranges,
                              tag_ids=[tag.result.id], note="旧说明\n保留换行")
    first = assets.create_annotation(create)
    second = assets.create_annotation(AnnotationCreate(request_id=uuid4(), work_id=work.id,
        source_ranges=ranges))
    update = AnnotationUpdate(request_id=uuid4(), work_id=work.id, annotation_id=first.result.id,
        expected_version=1, source_ranges=[ranges[1]], tag_ids=[], note="旧修改")
    changed = assets.update_annotation(update)
    query = AnnotationList(work_id=work.id, limit=1)
    page = assets.list_annotations(query)
    tables = {}
    with database.engine.connect() as connection:
        for table in metadata.sorted_tables:
            tables[table.name] = connection.execute(text(
                f"SELECT COALESCE(jsonb_agg(to_jsonb(t)), '[]'::jsonb) FROM {table.name} t"
            )).scalar_one()
    result = dict(source_hex=data.hex(), tables=tables, query=query.model_dump(mode="json"),
        cursor=page.next_cursor, next_id=str(second.result.id), annotation_id=str(first.result.id),
        requests=[dict(operation=operation, input=request.model_dump(mode="json"),
                       response=response.model_dump(mode="json"))
                  for operation, request, response in [
                      ("tag_create", tag_request, tag), ("annotation_create", create, first),
                      ("annotation_update", update, changed)]])
    Path(os.environ["FIXTURE_OUTPUT"]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
finally:
    database.close()
"""


def main() -> None:
    """导出固定提交的原代码到临时目录，子进程只从该目录导入旧服务。"""
    settings = load_settings()
    assert settings.database_url is not None
    revision = subprocess.check_output(
        ["git", "rev-parse", REVISION], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()
    files = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", revision, "src/novel_lens"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).splitlines()
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="assets-v1-") as temp:
        folder = Path(temp)
        for name in files:
            if name.endswith(".py"):
                target = folder / Path(name).relative_to("src")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(
                    subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=ROOT)
                )
        script = folder / "generate.py"
        script.write_text(CHILD, encoding="utf-8")
        output = Path(__file__).with_name("assets_0002.json")
        with temporary_database(settings.database_url.get_secret_value(), "0002") as url:
            env = dict(
                os.environ,
                NOVEL_LENS_DATABASE_URL=url,
                PYTHONUTF8="1",
                FIXTURE_OUTPUT=str(output),
                PYTHONPATH=str(folder),
            )
            subprocess.run([sys.executable, str(script)], cwd=ROOT, env=env, check=True)
        result = json.loads(output.read_text(encoding="utf-8"))
        result["generated_from_commit"] = revision
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Generated assets_0002.json from", revision)


if __name__ == "__main__":
    main()
