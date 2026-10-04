"""构建知识库索引。

用法（**必须用 `python -m`，不能直接执行脚本**）::

    python -m scripts.build_index
    python -m scripts.build_index --check-only   # 只查当前索引状态

## 为什么需要这个脚本

`app/knowledge/builder.py` 提供了 `build_index()`，
但**此前没有任何调用入口**——实测发现线上环境的 chroma 集合
`xuezhi_organic` 条数为 **0**，导致 `search_knowledge` 恒返回
`no_match`、端到端问答的 `sources` 恒为空（决策 H24）。

**缺陷的性质**：不是代码写错，而是**流程缺一步**。
代码侧一切正常（检索、嵌入、工具注册、超时实测均通过），
只是没人调用它。这类问题最难发现——所有测试都绿，
功能却完全不可用。

## 为什么不在服务启动时自动构建

**启动时自动灌索引有三个实际问题**：

1. **耗时**：实测 16 秒（41 条/ 21 个主题）。每次重启都等16 秒不可接受，
   且容器编排的健康检查会被拖累。
2. **多worker 竞态**：多个进程同时启动会**并发写同一个集合**，
   可能产生重复或损坏的索引。
3. **语料是代码**（已入git），索引是**派生产物**。
   语料变更时重建索引是**显式动作**，比"每次启动都重灌"更可控。

故做成独立命令，由 `docker-compose` 或人工触发。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 默认语料目录。**不得来自用户输入**（`security-privacy.md` §4）。
DEFAULT_CORPUS_DIR = REPO_ROOT / "data" / "knowledge" / "organic"

#: 默认索引持久化目录。
DEFAULT_PERSIST_DIR = REPO_ROOT / "data" / "chroma"

#: 默认集合名。须 3-512 字符且只含 ``[a-zA-Z0-9._-]``（实测 chromadb 约束）。
DEFAULT_COLLECTION = "xuezhi_organic"

#: 与 ``.env`` 的 ``XUEZHI_EMBEDDING_PATH`` 对齐：
#: 设了则离线加载，未设则在线从 HuggingFace 下载（需代理）。
ENV_EMBEDDING_PATH = "XUEZHI_EMBEDDING_PATH"


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.build_index",
        description="构建（或检查）知识库向量索引",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--persist", type=Path, default=DEFAULT_PERSIST_DIR)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument(
        "--index-version",
        default="unversioned",
        help="索引版本号，须与评估报告对应（interface-contract.md §7）",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只检查当前索引条数，不写入",
    )
    args = parser.parse_args(argv)

    # 与 pytest.ini 的 ``pythonpath = backend`` 保持一致
    backend_dir = REPO_ROOT / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    import os

    from app.rag.embeddings import SentenceTransformerEmbedding, resolve_local_model_path

    if args.check_only:
        return _check(args)

    if not args.corpus.is_dir():
        print(f"语料目录不存在：{args.corpus}", file=sys.stderr)
        return 1

    local = resolve_local_model_path()
    if not local:
        print(
            "提示：未设 " + ENV_EMBEDDING_PATH + "，将在线从 HuggingFace 下载嵌入模型。\n"
            "断网环境须先预置权重（见 docs/embedding-model-verification.md）。",
            file=sys.stderr,
        )

    from app.knowledge.builder import build_index

    embedding = SentenceTransformerEmbedding(
        local_files_only=local is not None,
        **({"cache_folder": local} if local else {}),
    )

    print(f"正在构建索引：{args.corpus} -> {args.persist}（集合 {args.collection}）")
    try:
        stats: dict[str, Any] = build_index(
            persist_directory=str(args.persist),
            corpus_directory=str(args.corpus),
            embedding=embedding,
            collection_name=args.collection,
            index_version=args.index_version,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"构建失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print("\n构建完成：")
    for key, value in stats.items():
        if key == "by_topic":
            continue
        print(f"  {key}: {value}")
    return 0


def _check(args: argparse.Namespace) -> int:
    """检查索引当前状态。

    刻意做成**只读**的独立路径：运维第一反应是"索引有没有建好"，
    而这个问题不需要加载嵌入模型（那要十几秒）。
    """
    try:
        import chromadb
    except ImportError:
        print("未装 chromadb，无法检查", file=sys.stderr)
        return 2

    client = chromadb.PersistentClient(path=str(args.persist))
    for col in client.list_collections():
        if col.name == args.collection:
            count = col.count()
            print(f"集合 {args.collection}：{count} 条")
            if count == 0:
                print(
                    "\n索引为空——检索将恒返回 no_match、问答来源恒为空。\n"
                    "请运行：python -m scripts.build_index",
                    file=sys.stderr,
                )
                return 1
            return 0
    print(f"集合 {args.collection} 不存在，请运行：python -m scripts.build_index", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
