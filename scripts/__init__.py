"""仓库脚本包。

**存在的唯一理由**：让 ``python -m scripts.export_openapi`` 可用。

``python -m package.module`` 要求 ``package`` 是一个包（含 ``__init__.py``），
否则报``No module named 'scripts'``。而直接执行
``python scripts/export_openapi.py`` 又会把 ``scripts/`` 放到
``sys.path`` 最前，导致 ``from app.api.app import ...`` 失败
（``app`` 在 ``backend/`` 下）。

有了这个文件，``python -m`` 就能把**当前工作目录**放最前，
两种调用方式的差异消失。见 :mod:`scripts.export_openapi` 的模块说明。
"""
