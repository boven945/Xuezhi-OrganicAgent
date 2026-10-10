#!/usr/bin/env bash
# 演示前自检。
#
# ## 为什么需要这个脚本（实测教训，2026-10-10）
#
# 本项目当天连踩四次「`/health` 说就绪、实际不可用」：
#
#   | 探针报| 实际 |
#   | --- | --- |
#   | `tts=就绪`    | edge-tts 根本没装 |
#   | `rag=已加载`  | 嵌入模型 ProxyError，检索全挂 |
#   | `fay=就绪`    | Fay 端口 10002 无监听 |
#   | `rag=已加载`  | 路径被当成 cache_folder 传，构造成功但检索必失败 |
#
# 前四次都**只看了 `/health`**。**光看它不够——必须真跑一遍。**
#
# 本脚本不看`/health` 的 ready 字段（那正是会说谎的那个），
# 而是真的发请求、看响应内容，据此判定。
#
# 用法：
#   ./scripts/demo-check.sh              # 检查默认地址
#   BACKEND=http://127.0.0.1:8000 ./scripts/demo-check.sh
#
# 退出码：0 全部通过；1 有失败项（**演示前应先看这个**）

set -uo pipefail

BACKEND="${BACKEND:-http://127.0.0.1:8000}"
# **必须绕过代理**：本机HTTP 代理会把对 127.0.0.1 的请求也丢给代理，
# 返回 502，表现为「服务在跑却报不可用」（实测，与fay.py 同源问题）。
CURL=(curl -s --noproxy '*' --max-time 180)

if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'
else
  R=""; G=""; Y=""; D=""; N=""
fi
PASS=0
FAIL=0

ok()   { echo "${G}✓${N} $*"; PASS=$((PASS+1)); }
bad()  { echo "${R}✗${N} $*"; FAIL=$((FAIL+1)); }
skip() { echo "${Y}-${N} $*（已跳过）"; }
dim()  { echo "${D}$*${N}"; }

echo "学智有机 · 演示前自检"
echo "─────────────────────────────────────────"
echo "  后端 ${BACKEND}"
echo

# ----------------------------------------------------------------------
# 1. 服务可达
# ----------------------------------------------------------------------
health=$("${CURL[@]}" "$BACKEND/health" 2>/dev/null)
if [ -z "$health" ]; then
  bad "后端不可达（先跑 scripts/run-local.sh）"
  echo
  echo "无法继续检查。"; exit 1
fi
ok "后端可达"

# ----------------------------------------------------------------------
# 2. 各组件实测能力（不信 ready，只信 *_verified）
# ----------------------------------------------------------------------
# speech 与 rag 都要检查。
# **判据只用 *_verified**（实测层），配置层仅用于决定「是否应当报故障」——
# 用户没启用 Fay 不是故障，那是用户的选择。
dim "组件实测："
caps_out=$(
  echo "$health" | python -c "
import json, sys

def b(v):
    # **None 必须映射成 false 而不是字符串 'none'**——
    # str(None).lower() 是 'none'，会被当成「既非 true 也非 false」而漏判。
    return 'true' if v is True else 'false'

try:
    h = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for c in h.get('components', []):
    caps = c.get('caps') or {}
    if c['name'] == 'speech':
        print('语音合成', b(caps.get('tts')), b(caps.get('tts_verified')))
        print('数字人推送', b(caps.get('fay')), b(caps.get('fay_verified')))
    if c['name'] == 'rag':
        # rag 没有「启停」概念：一旦配了就应可用，故配置层恒 true
        print('知识检索', 'true', b(caps.get('rag_verified')))
" 2>/dev/null
)

if [ -z "$caps_out" ]; then
  bad "无法解析 /health 的组件信息（后端版本可能不兼容）"
else
  # **必须剥掉 CR**：Windows 上Python 的 print 输出 CRLF，
  # 于是 $verified 变成 "true\r"，与"true" 比对恒假——
  # 实测踩过：明明全部实测通过，脚本却报2 项未通过。
  # `read -r` 不吞 \r，`tr -d '\r'` 才有效。
  while read -r label configured verified; do
    label=$(printf '%s' "$label" | tr -d '\r')
    configured=$(printf '%s' "$configured" | tr -d '\r')
    verified=$(printf '%s' "$verified" | tr -d '\r')
    [ -z "${label:-}" ] && continue
    if [ "$configured" = "true" ]; then
      if [ "$verified" = "true" ]; then
        ok "$label 实测通过"
      else
        bad "$label **实测未通过**（配置为启用，实际不可用）"
      fi
    else
      skip "$label 未启用"
    fi
  done <<< "$caps_out"
fi

# ----------------------------------------------------------------------
# 3. 真问答（**不信 /health，必须真跑**）
# ----------------------------------------------------------------------
dim ""
dim "端到端："
ask=$("${CURL[@]}" -X POST "$BACKEND/api/v1/ask" \
  -H "Content-Type: application/json" \
  -d '{"question":"苯酚为什么具有弱酸性？"}' 2>/dev/null)

if [ -z "$ask" ]; then
  bad "问答接口无响应"
# **措辞来自实测，不能凭印象写**（实测踩过）：
# 模型实际输出的是「检索服务**暂时**不可用」，
# 我原先写的 `检索服务(当前)?不可用` 匹配不到——
# 「当前」两个字在这句话里根本不存在。
# 现按真实措辞列出多种变体，并用 `检索.*不可用`兜住同义表述。
elif echo "$ask" | grep -qE "检索服务(暂时|当前)?不可用|检索.*不可用|无法连接|嵌入模型加载失败|未能从教材检索"; then
  # **关键检查**：答复正文自己说了检索不可用。
  # 这种情况下 `/health` 仍可能报 rag=已加载——必须看内容。
  bad "检索实际不可用（答复自述检索失败，但/health 可能仍报就绪）"
elif [ "${#ask}" -lt 200 ]; then
  bad "答复过短（${#ask} 字节），可能生成失败"
else
  has_src=$(echo "$ask" | grep -c '"source_id"' || true)
  if [ "$has_src" -gt 0 ]; then
    ok "问答可用且带教材出处（source_id 命中 $has_src 处）"
  else
    bad "答复无 source_id——检索可能没生效（这是演示的卖点之一）"
  fi
fi

# ----------------------------------------------------------------------
# 4. 语音合成（真合成，看有没有音频）
# ----------------------------------------------------------------------
dim ""
dim "语音："
speak=$("${CURL[@]}" -X POST "$BACKEND/api/v1/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"乙醇分子式为C2H5OH。","user":"demo-check"}' 2>/dev/null)

if [ -z "$speak" ]; then
  bad "语音接口无响应"
else
  audio_id=$(echo "$speak" | python -c "
import json, sys
try: print(json.load(sys.stdin).get('audio_id') or '')
except Exception: print('')
" 2>/dev/null)
  if [ -z "$audio_id" ]; then
    reason=$(echo "$speak" | python -c "
import json, sys
try: print(json.load(sys.stdin).get('reason') or '(无原因)')
except Exception: print('(响应无法解析)')
" 2>/dev/null)
    bad "语音合成失败：${reason}"
  else
    # 真取一次音频——只拿到 audio_id 不代表文件可读
    size=$("${CURL[@]}" -o /dev/null -w '%{size_download}' \
      "$BACKEND/api/v1/speak/$audio_id" 2>/dev/null)
    if [ "${size:-0}" -gt 1000 ]; then
      ok "语音可用（音频 $size 字节）"
    else
      bad "语音返回了 audio_id 但取不到音频（仅 $size 字节）"
    fi
  fi
fi

# ----------------------------------------------------------------------
# 5. 分子解析（演示的另一个卖点）
# ----------------------------------------------------------------------
dim ""
dim "化学："
mol=$("${CURL[@]}" -X POST "$BACKEND/api/v1/molecule" \
  -H "Content-Type: application/json" \
  -d '{"smiles":"Oc1ccccc1"}' 2>/dev/null)
if [ -z "$mol" ]; then
  bad "分子解析接口无响应"
elif echo "$mol" | grep -qE '"(atoms|bonds)"[[:space:]]*:[[:space:]]*\[[^]]'; then
  ok "分子解析可用"
else
  bad "分子解析未返回 atoms/bonds 数组"
fi

echo
echo "─────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
  echo "${G}全部通过（$PASS 项）${N}——可以演示"
  exit 0
else
  echo "${R}有 $FAIL 项失败${N}（通过 $PASS 项）—— ${Y}建议先修复再演示${N}"
  exit 1
fi