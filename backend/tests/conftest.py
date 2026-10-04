"""pytest 全局配置与环境能力标记。

## 为什么需要 conftest

``backend/tests/`` **不是包**（无 ``__init__.py``），
因此测试文件之间不能互相 import。pytest 会把 conftest 所在目录
加入 ``sys.path``，故各测试文件可写 ``from _env import has_chem``。

## 提供的标记

- ``skip_if_no_chem``：RDKit 不可用时跳过。
  供 ``pytest.mark.usefixtures`` 或直接作为装饰器使用。

多数情况下更推荐**直接**写::

    @pytest.mark.skipif(not has_chem(), reason=chem_block_reason())
    class TestXxx: ...

显式胜于隐式——跳过原因在测试文件里一目了然。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 把 tests 目录加入 sys.path，使各测试文件能 import _env
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _env import chem_block_reason, has_chem  # noqa: E402

#: 依赖 RDKit 的测试文件。
#:
#: **为何需要 ``collect_ignore`` 而不是只用 ``skipif``**：
#: 这些文件在**模块级**就 ``from app.chem.engine import ChemEngine``，
#: RDKit 不可用时 pytest 在**收集阶段**就报ERROR——
#: 此时 ``skipif`` 还没机会生效，整个文件无法收集。
#:
#: 实测（2026-10-04 本机WDAC 拦截 rdchem）：
#: 未加此机制时 3 个文件收集失败，全量测试直接中断。
CHEM_DEPENDENT_FILES = (
    "chem/test_engine.py",
    "chem/test_functional_groups.py",
    "api/test_app.py",
    "api/test_e2e_http.py",
)


def pytest_ignore_collect(collection_path, config):
    """RDKit 不可用时，跳过依赖它的测试文件的**收集**。

    与 ``skipif`` 的分工：
    - ``collect_ignore``：整个文件不收集（文件顶层就import 失败时用）
    - ``skipif``：文件能收集但部分用例不适用时用

    Returns:
        True 表示忽略该文件。
    """
    if has_chem():
        return False
    rel = str(collection_path).replace("\\", "/")
    return any(rel.endswith(name) for name in CHEM_DEPENDENT_FILES)

#: 供``@pytest.mark.skipif`` 使用的条件。
skip_if_no_chem = pytest.mark.skipif(
    not has_chem(),
    reason=chem_block_reason() or "RDKit 不可用",
)


@pytest.fixture(scope="session")
def chem_available() -> bool:
    """化学引擎是否可用。用例内可据此决定断言强度。"""
    return has_chem()
