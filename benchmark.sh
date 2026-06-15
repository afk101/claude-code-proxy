#!/bin/bash
# benchmark.sh - 测量 proxy_models.conf 中各模型的 TTFB 和响应速度
#
# 用法:
#   ./benchmark.sh                    # 默认参数
#   ./benchmark.sh --prompt Hello     # 自定义 prompt
#   ./benchmark.sh --max-tokens 20    # 生成更多 token
#   ./benchmark.sh --concurrency 2    # 降低并发
#   ./benchmark.sh --format json      # 输出 JSON
#
# API Key 从 .env 文件读取 OPENAI_API_KEY 和 OPENAI_BASE_URL

set -eo pipefail

# ── 默认参数 ──
PROMPT="Hi"
MAX_TOKENS=10
CONCURRENCY=3
FORMAT="text"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/proxy_models.conf"
ENV_FILE="${SCRIPT_DIR}/.env"

# ── 解析命令行参数 ──
while [[ $# -gt 0 ]]; do
    case $1 in
        --prompt)        PROMPT="$2"; shift 2 ;;
        --max-tokens)    MAX_TOKENS="$2"; shift 2 ;;
        --concurrency)   CONCURRENCY="$2"; shift 2 ;;
        --format)        FORMAT="$2"; shift 2 ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --prompt TEXT       测试 prompt (默认: Hi)"
            echo "  --max-tokens N      最大生成 token 数 (默认: 10)"
            echo "  --concurrency N     并发测试数 (默认: 3)"
            echo "  --format FMT        输出格式: text/json (默认: text)"
            exit 0 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# ── 读取 .env ──
if [[ ! -f "$ENV_FILE" ]]; then
    echo "错误: 未找到 .env 文件: $ENV_FILE" >&2
    exit 1
fi

API_KEY=""
BASE_URL=""
while IFS='=' read -r key value; do
    key="$(echo "$key" | tr -d '[:space:]')"
    [[ "$key" =~ ^# ]] && continue
    [[ -z "$key" ]] && continue
    value="$(echo "$value" | sed 's/^"//;s/"$//')"
    case "$key" in
        OPENAI_API_KEY)  API_KEY="$value" ;;
        OPENAI_BASE_URL) BASE_URL="$value" ;;
    esac
done < "$ENV_FILE"

if [[ -z "$API_KEY" ]]; then
    echo "错误: .env 中未找到 OPENAI_API_KEY" >&2
    exit 1
fi
if [[ -z "$BASE_URL" ]]; then
    BASE_URL="https://api.openai.com/v1"
fi

# ── 解析 proxy_models.conf ──
if [[ ! -f "$CONF_FILE" ]]; then
    echo "错误: 未找到模型配置文件: $CONF_FILE" >&2
    exit 1
fi

# 提取 model 和 name 的对应关系（两个平行数组）
MODELS=()
NAMES=()
_PENDING_NAME=""
_SEEN=""
while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$line" ]] && continue
    if [[ "$line" =~ ^name=(.+)$ ]]; then
        _PENDING_NAME="${BASH_REMATCH[1]}"
    fi
    if [[ "$line" =~ ^model=(.+)$ ]]; then
        m="${BASH_REMATCH[1]}"
        if [[ "$_SEEN" != *"|$m|"* ]]; then
            _SEEN="$_SEEN|$m|"
            MODELS+=("$m")
            NAMES+=("${_PENDING_NAME:-$m}")
        fi
        _PENDING_NAME=""
    fi
done < "$CONF_FILE"

if [[ ${#MODELS[@]} -eq 0 ]]; then
    echo "错误: proxy_models.conf 中未找到模型配置" >&2
    exit 1
fi

# ── 显示测试概要 ──
echo "Benchmark 模型测速"
echo "─────────────────"
echo "模型数量: ${#MODELS[@]}  并发数: ${CONCURRENCY}  prompt=\"${PROMPT}\"  max_tokens=${MAX_TOKENS}"
echo "上游: ${BASE_URL}"
echo ""

# ── 并发执行测速 ──
# 后台任务只做 curl，主进程读取结果
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

BATCH_START=0
BATCH_NUM=0
TOTAL_BATCHES=$(( (${#MODELS[@]} + CONCURRENCY - 1) / CONCURRENCY ))
while [[ $BATCH_START -lt ${#MODELS[@]} ]]; do
    BATCH_END=$((BATCH_START + CONCURRENCY))
    [[ $BATCH_END -gt ${#MODELS[@]} ]] && BATCH_END=${#MODELS[@]}
    BATCH_NUM=$((BATCH_NUM + 1))

    echo -n "测速中 [${BATCH_NUM}/${TOTAL_BATCHES}]"
    for i in $(seq $BATCH_START $((BATCH_END - 1))); do
        model="${MODELS[$i]}"
        echo -n "  ${model}"
    done
    echo " ..."

    for i in $(seq $BATCH_START $((BATCH_END - 1))); do
        model="${MODELS[$i]}"
        safe_name="${model//\//_}"
        # 后台 curl：只写 timing 数据到文件
        curl -s -N \
            -w "__TIMING__|ttfb=%{time_starttransfer}|total=%{time_total}" \
            -o /dev/null \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${API_KEY}" \
            -d "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"${PROMPT}\"}],\"max_tokens\":${MAX_TOKENS},\"stream\":true}" \
            "${BASE_URL}/chat/completions" \
            > "${TEMP_DIR}/${safe_name}" 2>/dev/null &
    done

    # 等待当前批次
    wait

    BATCH_START=$BATCH_END
done

echo "测速完成，正在汇总结果..."
echo ""

# ── 显示宽度计算（修正 CJK 双宽字符对齐） ──
# printf 按字符计数，但 CJK/全角字符占 2 个终端列
# 使用 python3 的 unicodedata.east_asian_width 精确计算
visual_width() {
    printf '%s' "$1" | python3 -c '
import sys, unicodedata
s = sys.stdin.read()
print(sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s))
'
}

# 左对齐：字符串 + 补齐空格
pad_left() {
    local s="$1" w="$2"
    local vw
    vw=$(visual_width "$s")
    local pad=$(( w - vw ))
    [[ $pad -lt 0 ]] && pad=0
    printf '%s%*s' "$s" "$pad" ""
}

# 右对齐：补齐空格 + 字符串
pad_right() {
    local s="$1" w="$2"
    local vw
    vw=$(visual_width "$s")
    local pad=$(( w - vw ))
    [[ $pad -lt 0 ]] && pad=0
    printf '%*s%s' "$pad" "" "$s"
}

# ── 解析结果 ──
RESULTS=()
for i in "${!MODELS[@]}"; do
    model="${MODELS[$i]}"
    upstream="${NAMES[$i]}"
    safe_name="${model//\//_}"
    local_file="${TEMP_DIR}/${safe_name}"

    if [[ ! -f "$local_file" || ! -s "$local_file" ]]; then
        RESULTS+=("${model}|${upstream}|0|0|error|请求失败")
        continue
    fi

    timing_line=$(cat "$local_file")
    ttfb=$(echo "$timing_line" | grep -o 'ttfb=[0-9.]*' | cut -d= -f2)
    total=$(echo "$timing_line" | grep -o 'total=[0-9.]*' | cut -d= -f2)

    if [[ -z "$ttfb" || "$ttfb" == "0.000000" || "$ttfb" == "0" ]]; then
        RESULTS+=("${model}|${upstream}|0|0|error|请求失败")
        continue
    fi

    ttfb_ms=$(awk "BEGIN{printf \"%.1f\", $ttfb * 1000}")
    total_ms=$(awk "BEGIN{printf \"%.1f\", $total * 1000}")
    tps=$(awk "BEGIN{if($total > 0) printf \"%.2f\", $MAX_TOKENS / $total; else print \"0\"}")

    RESULTS+=("${model}|${upstream}|${ttfb_ms}|${total_ms}|success|${tps}")
done

# ── 按 TTFB 排序 ──
IFS=$'\n' SORTED=($(printf '%s\n' "${RESULTS[@]}" | sort -t'|' -k3 -n)); unset IFS

# ── 分档函数 ──
classify_tier() {
    if awk "BEGIN{exit !($1 < 1500)}"; then echo "快（<1.5s）"
    elif awk "BEGIN{exit !($1 < 3000)}"; then echo "中（1.5~3s）"
    elif awk "BEGIN{exit !($1 < 6000)}"; then echo "慢（3~6s）"
    else echo "极慢（>6s）"
    fi
}

# ── 描述函数 ──
describe() {
    local ttfb_ms="$1" total_ms="$2" tps="$3" is_fastest="$4"
    local parts=""

    [[ "$is_fastest" == "1" ]] && parts="最快"

    # 推理模型：TTFB ≈ total（差值 <5%）且 total > 3s
    if [[ "$total_ms" != "0" ]] && awk "BEGIN{exit !($total_ms > 3000)}"; then
        if awk "BEGIN{exit !(($total_ms - $ttfb_ms) / $total_ms < 0.05)}"; then
            parts="${parts:+${parts}，}推理模型"
        fi
    fi

    if [[ -n "$tps" && "$tps" != "0" && "$tps" != "0.00" ]]; then
        local tps_desc
        if awk "BEGIN{exit !($tps >= 15)}"; then tps_desc="吞吐 ${tps} tok/s"
        elif awk "BEGIN{exit !($tps <= 2)}"; then tps_desc="仅 ${tps} tok/s"
        else tps_desc="${tps} tok/s"
        fi
        parts="${parts:+${parts}，}${tps_desc}"
    fi

    [[ -z "$parts" ]] && parts="-"
    echo "$parts"
}

# ── JSON 输出 ──
if [[ "$FORMAT" == "json" ]]; then
    echo "{"
    echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"prompt\": \"${PROMPT}\","
    echo "  \"max_tokens\": ${MAX_TOKENS},"
    echo "  \"results\": ["
    first=1
    for row in "${SORTED[@]}"; do
        IFS='|' read -r model upstream ttfb_ms total_ms status tps <<< "$row"
        [[ $first -eq 0 ]] && echo ","
        first=0
        printf '    {"model":"%s","upstream_name":"%s","ttfb_ms":%s,"total_ms":%s,"tokens_per_second":%s,"status":"%s"}' \
            "$model" "$upstream" "$ttfb_ms" "$total_ms" "$tps" "$status"
    done
    echo ""
    echo "  ]"
    echo "}"
    exit 0
fi

# ── 表格输出 ──
echo "Benchmark 结果 (prompt=\"${PROMPT}\", max_tokens=${MAX_TOKENS})"
echo "测试时间: $(date '+%Y-%m-%d %H:%M:%S')  上游: ${BASE_URL}"
echo ""

# 找出最快模型
FASTEST_MODEL=""
for row in "${SORTED[@]}"; do
    IFS='|' read -r model upstream ttfb_ms total_ms status tps <<< "$row"
    if [[ "$status" == "success" && "$ttfb_ms" != "0" ]]; then
        FASTEST_MODEL="$model"
        break
    fi
done

# 构建行数据
ROWS=()
PREV_TIER=""
for row in "${SORTED[@]}"; do
    IFS='|' read -r model upstream ttfb_ms total_ms status tps <<< "$row"

    if [[ "$status" != "success" || "$ttfb_ms" == "0" ]]; then
        tier="失败"; ttfb_str="-"; char="请求失败"
    else
        tier=$(classify_tier "$ttfb_ms")
        ttfb_str="$(printf '%.0f' "$ttfb_ms")ms"
        is_fastest=0
        [[ "$model" == "$FASTEST_MODEL" ]] && is_fastest=1
        char=$(describe "$ttfb_ms" "$total_ms" "$tps" "$is_fastest")
    fi

    model_display="$model"
    [[ -n "$upstream" && "$upstream" != "$model" ]] && model_display="$model ($upstream)"

    # 同档位合并
    if [[ "$tier" == "$PREV_TIER" ]]; then tier=""; else PREV_TIER="$tier"; fi

    ROWS+=("${tier}|${model_display}|${ttfb_str}|${char}")
done

# 计算列宽
COL1_W=14; COL2_W=36; COL3_W=9; COL4_W=24
for row in "${ROWS[@]}"; do
    IFS='|' read -r c1 c2 c3 c4 <<< "$row"
    [[ $(visual_width "$c1") -gt $COL1_W ]] && COL1_W=$(visual_width "$c1")
    [[ $(visual_width "$c2") -gt $COL2_W ]] && COL2_W=$(visual_width "$c2")
    [[ $(visual_width "$c4") -gt $COL4_W ]] && COL4_W=$(visual_width "$c4")
done

# 绘制
draw_sep() {
    local left="$1" mid="$2" right="$3"
    printf '%s' "$left"
    printf '%0.s─' $(seq 1 $((COL1_W + 2)))
    printf '%s' "$mid"
    printf '%0.s─' $(seq 1 $((COL2_W + 2)))
    printf '%s' "$mid"
    printf '%0.s─' $(seq 1 $((COL3_W + 2)))
    printf '%s' "$mid"
    printf '%0.s─' $(seq 1 $((COL4_W + 2)))
    printf '%s\n' "$right"
}

draw_row() {
    printf '│ '
    pad_left "$1" "$COL1_W"
    printf ' │ '
    pad_left "$2" "$COL2_W"
    printf ' │ '
    pad_right "$3" "$COL3_W"
    printf ' │ '
    pad_left "$4" "$COL4_W"
    printf ' │\n'
}

draw_sep '┌' '┬' '┐'
draw_row "档位" "模型" "TTFB" "特点"
draw_sep '├' '┼' '┤'
for row in "${ROWS[@]}"; do
    IFS='|' read -r c1 c2 c3 c4 <<< "$row"
    draw_row "$c1" "$c2" "$c3" "$c4"
done
draw_sep '└' '┴' '┘'
