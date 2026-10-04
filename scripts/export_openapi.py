"""导出静态 OpenAPI 契约。

用法（**必须用 `python -m`，不能直接执行脚本**）::

    python -m scripts.export_openapi
    python -m scripts.export_openapi --check
    python -m scripts.export_openapi --out other.json

## 为什么用 ``python -m`` 而非 ``python scripts/export_openapi.py``

直接执行脚本时，Python 会把 ``scripts/`` 放到``sys.path`` 最前面，
于是 ``from app.api.app import create_app`` 会失败
（``app`` 不在``scripts/`` 下，而在 ``backend/`` 下）——
而同样的导入在pytest 或 uvicorn 里是正常的。

实测踩过：这类错误在本地直接跑脚本时才暴露，
换到 CI 里就变成看不懂的 ``ModuleNotFoundError``。
``python -m`` 把**当前工作目录**放最前，行为可预期。

## 为什么要 ``sort_keys=True``

不排序的话，字典插入顺序变化就会产生 diff 噪声——
审阅者要在格式变化里找真正的契约变化。
排序后，**diff 里出现的每一处都是真实的契约变更**。

同理``indent=2`` 固定缩进、末尾补一个换行（POSIX 文本文件惯例）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: 仓库根目录。本文件在 ``scripts/`` 下，故上一级即根。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 契约文件的默认位置。
#:
#: **放``docs/api/`` 而非仓库根**：契约是对外的接口基线，
#: 属文档体系的一部分（`docs/interface-contract.md` 是它的说明）。
#: 放根目录会让根目录再添一个零散文件。
DEFAULT_OUT = REPO_ROOT / "docs" / "api" / "openapi.json"


def build_spec() -> dict[str, Any]:
    """生成 OpenAPI 字典。

    刻意**新建 app 实例**而非 import 模块级的 ``app``：
    模块级 ``app = create_app()`` 在 import 时就会读环境变量，
    一旦本机 ``.env`` 有配置就会产出与CI 不同的契约。
    显式构造让输出只取决于代码。
    """
    # 与 pytest.ini 的 ``pythonpath = backend`` 保持一致
    backend_dir = REPO_ROOT / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.api.app import create_app

    app = create_app()
    return app.openapi()


def serialize(spec: dict[str, Any]) -> str:
    """把spec 序列化成**确定性**的文本。

    三处确定性选择（缺一项就会产生格式噪声）：
    - ``sort_keys=True``：键排序，消除插入顺序差异
    - ``indent=2``：固定缩进
    - 末尾 ``\\n``：POSIX 文本文件惯例，也避免 ``\\ No newline`` 标记
    """
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _describe_diff(current: str, committed: str) -> str:
    """生成人类可读的差异说明。

    刻意**不逐行diff**：契约文件动辄上千行，
    逐行 diff 会淹没真正重要的信息（少了哪个端点、哪个字段）。
    只报告「规模变了多少」与「端点集合的差异」——
    这才是审阅者要先看的。
    """
    lines: list[str] = []
    cur = json.loads(current)
    old = json.loads(committed)

    def paths(spec: dict[str, Any]) -> set[str]:
        return set(spec.get("paths", {}))

    cur_paths, old_paths = paths(cur), paths(old)
    added, removed = sorted(cur_paths - old_paths), sorted(old_paths - cur_paths)
    if added:
        lines.append(f"  新增端点：{added}")
    if removed:
        lines.append(f"  **删除端点：{removed}**")
    if not added and not removed:
        lines.append("  端点集合未变（差异在字段层）")

    cur_schemas = set(cur.get("components", {}).get("schemas", {}))
    old_schemas = set(old.get("components", {}).get("schemas", {}))
    new_s = sorted(cur_schemas - old_schemas)
    gone_s = sorted(old_schemas - cur_schemas)
    if new_s:
        lines.append(f"  新增 schema：{new_s}")
    if gone_s:
        lines.append(f"  **删除 schema：{gone_s}**")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Returns:
        进程退出码。``--check`` 模式下不一致返回 1。
    """
    parser = argparse.ArgumentParser(
        prog="python -m scripts.export_openapi",
        description="导出或校验静态 OpenAPI 契约",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"输出路径（默认 {DEFAULT_OUT.relative_to(REPO_ROOT)}）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验已提交的文件是否与当前代码一致，不写入",
    )
    args = parser.parse_args(argv)

    try:
        spec = build_spec()
    except Exception as exc:  # noqa: BLE001
        print(f"生成 OpenAPI 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    content = serialize(spec)
    out_path: Path = args.out
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path

    if not args.check:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        n_paths = len(spec.get("paths", {}))
        n_schemas = len(spec.get("components", {}).get("schemas", {}))
        print(f"已写入 {out_path.relative_to(REPO_ROOT)}：{n_paths} 端点 / {n_schemas} schema")
        return 0

    # ---- --check 模式 ----
    if not out_path.exists():
        print(
            f"契约文件不存在：{out_path.relative_to(REPO_ROOT)}\n"
            f"请运行：python -m scripts.export_openapi",
            file=sys.stderr,
        )
        return 1

    committed = out_path.read_text(encoding="utf-8")
    if committed == content:
        print(f"契约一致：{out_path.relative_to(REPO_ROOT)}")
        return 0

    print("**契约已变更**（代码与已提交文件不一致）", file=sys.stderr)
    print(_describe_diff(content, committed), file=sys.stderr)
    print(
        "\n若变更是预期的，请更新后重跑：\n"
        "  python -m scripts.export_openapi",
        file=sys.stderr,
    )
    print(
        "\n若**不**预期，说明有人改了接口却没更新契约——"
        "前端可能因此出错（见 docs/interface-contract.md）。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
