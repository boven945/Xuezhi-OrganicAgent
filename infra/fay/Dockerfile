# Fay 数字人服务的容器运行镜像
#
# ## 为什么用容器而不是本机 venv
#
# 本机 Smart App Control 按签名拦截未签名二进制（决策 H12），
# 而 Fay 的依赖里有大量原生包：pyaudio、opencv-python、pygame、
# scipy、chromadb 拉入的 tokenizers 等。实测本机装RDKit 时
# 63 个 .pyd 里 56 个被拦——Fay 的原生依赖只会更糟。
#
# 容器内二进制不受主机策略管辖（已由 xuezhi-chem-test 验证）。
#
# ## 口径问题（必须先说清）
#
# **容器内 Fay 不会生成口型数据。** 源码 `core/fay_core.py:2270`：
#
# ```python
# if platform.system() == "Windows":
#     lip_sync_generator = LipSyncGenerator()
#     ...
#     content["Data"]["Lips"] = consolidated_visemes
# ```
#
# 口型靠 `test/ovr_lipsync/ovr_lipsync_exe/ProcessWAV.exe`
# 从 WAV 离线分析，**仅在 Windows 上执行**。
# 故Linux 容器里 `Lips` 恒为空，前端口型走本地近似
# （`useSpeechPlayback` 的兜底切换，已实测可用）。
#
# **这不是 bug，是 Fay 的平台限定。** 想要真实 viseme 需在
# Windows 上直接跑 Fay（不容器化）——但那受本机策略影响，
# 需实测能否装上。已登记为待验证项，不在这里假装能行。
#
# ## 构建
#
# 从**仓库根目录**构建（Fay 源码不在本仓库内）：
#
# ```bash
# docker build -f infra/fay/Dockerfile -t xuezhi-fay F:/fay-run
# ```
#
# 上下文必须是 Fay 的克隆目录，因为要COPY 它自己的源码。

FROM python:3.12-slim

# 系统依赖。
#
# ## build-essential 与 portaudio19-dev 不能省（实测踩过）
#
# 我曾推断"pyaudio 只在 ASR 里import，我们不用，
# 应该可以不装编译工具链"——**推断错了**，构建报
# `Failed to build installable wheels ... (pyaudio)`。
#
# 根因：**pyaudio 在 Linux 上没有预编译 wheel**。
# 实测 PyPI 0.2.14 只有 `cp312-win32` 与 `cp312-win_amd64`，
# manylinux 一个都没有。故 Linux 上必须
#装 portaudio 开发头 + C/C++ 工具链现场编译。
#
# > **别凭"应该不需要"下判断**——先看那个包在目标平台
# > 有没有 wheel。
#
# libgl1/libglib2.0-0 是 opencv-python 的运行时依赖，
# 缺了 `import cv2` 直接失败（实测）。
#
# apt 加三次重试：实测遇到过 deb.debian.org 返回 502 Bad Gateway，
# 而包名与源都没问题——是网络抖动，重试即可。
RUN set -eux; \
    apt-get update; \
    for i in 1 2 3; do \
        if apt-get install -y --no-install-recommends \
            build-essential \
            portaudio19-dev \
            libgl1 \
            libglib2.0-0 \
            ffmpeg \
            curl \
        ; then break; fi; \
        echo "apt 第 $i 次失败，重试（网络抖动）"; \
        sleep 5; \
    done; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖再拷源码：源码层变动不应触发依赖重装。
# 官方 requirements.txt **不锁版本**（实测确认），
# 故这里也不锁——但记录镜像构建日期，便于日后复现同一环境。
COPY requirements.txt ./

# 上游 requirements.txt 有一处已过时（实测 2026-10-05）：
#
# ```text
# ERROR: Cannot install langgraph and websockets~=10.4
#   langgraph-sdk 0.4.5 depends on websockets<17 and >=14
#   The user requested websockets~=10.4
# ```
#
# 即 Fay 写死 `websockets~=10.4`（<11），而它自己依赖的
# langgraph-sdk 要求 >=14。**上游清单没跟上自己的新依赖**。
#
# ## 为什么降级 langgraph-sdk 而不是放宽 websockets（实测踩坑）
#
# 我第一版选择放宽 websockets 到 >=14，**结果 10002 端口起不来**：
#
# ```text
# File "/app/core/wsa_server.py", line 198, in __connect
#     self.__server = websockets.serve(...)
# RuntimeError: no running event loop
# ```
#
# 根因：Fay 用 `websockets.serve()` **同步 API**，
# 该 API 在 websockets 14 中已被移除（迁到 `websockets.sync.server`）。
# **放宽版本反而把服务改坏了** —— 这是"修 A弄坏 B"的典型。
#
# 正确解法是**降 langgraph-sdk**：实测 <= 0.3.15 的
# langgraph-sdk **不依赖 websockets**，冲突自然消失，
# 且Fay 那个同步 API 保持原样可用。
#
# 改动只作用于**容器内那份** requirements，**不改 Fay 源码**
#（GPL-3.0，且我们本就不该改第三方代码）。
# 先装 SDK 再装 requirements —— pip 会看到约束已满足而不重复解析。
# ## 为什么同时锁 langgraph 与 langgraph-sdk（实测两轮才明白）
#
# 第一轮只锁 `langgraph-sdk==0.3.15` → **无效**：
# requirements 里还有无版本约束的 `langgraph`，它会拉最新版 SDK，
# 于是 0.4.x 又回来了，冲突复现。
#
# 实测各版本对 SDK 的要求：
# ```text
# langgraph 0.2.60 → langgraph-sdk (>=0.1.42,<0.2.0)   ← 该区间不依赖 websockets
# langgraph 0.3.5  → langgraph-sdk (>=0.1.42,<0.2.0)   ← 同上
# langgraph 0.4.0  → langgraph-sdk (>=0.1.42)          ← 无上界，会拉 0.4.x
# ```
#
# 故**两个都要锁**：
# - `langgraph==0.3.5`（上界有 <0.2.0）
# - `langgraph-sdk==0.1.74`（落在该区间内，且实测不依赖 websockets）
#
# **必须在同一条命令里**（实测踩过）：
# 我曾拆成两条——第一条装 langgraph+SDK，第二条装 requirements，
# 结果第二条**丢掉了锁定**（锁定只在单条命令内有效），
# 于是它又要装最新版langgraph，冲突复现。
# dry-run 验证时用的一条命令能过，实际写成分两条就挂——
# **验证用的形式必须与实现完全一致**。
#
# dry-run 实测该组合解析成功，且 websockets 落在 10.4（Fay 要的版本）。
RUN pip install --no-cache-dir \
        "langgraph==0.3.5" "langgraph-sdk==0.1.74" \
        -r requirements.txt && \
    python -c "import websockets, langgraph_sdk; print('websockets', websockets.__version__)"

# 拷源码
COPY . .

# 配置文件：官方要求把 system.conf.bak 重命名为 system.conf
RUN cp -n system.conf.bak system.conf || true

# 端口（核实自 main.py）：
# - 10002：WebSocket，数字人渲染端接这个
# - 10003：WebSocket，UI 数据用
# - 5000：Flask HTTP，含 /transparent-pass 与管理页
EXPOSE 10002 10003 5000

# 与官方 README 一致。注意 start_mode=web 已在配置里，
# 容器内不能走 GUI 模式（无显示设备）。
CMD ["python", "main.py", "start"]
