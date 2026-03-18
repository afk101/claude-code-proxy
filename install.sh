#!/bin/bash
# 统一安装 Python 和 Node.js 依赖

# 获取脚本所在目录（处理符号链接）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "=== 安装项目依赖 ==="
echo ""

# 1. Python 依赖（uv）
echo "--- 安装 Python 依赖 (uv) ---"
if ! command -v uv &> /dev/null; then
    echo "错误: 未找到 uv，请先安装 uv (https://docs.astral.sh/uv/)"
    exit 1
fi
# 创建虚拟环境（如果不存在）
if [ ! -d ".venv" ]; then
    echo "创建 Python 虚拟环境..."
    uv venv
fi
# 安装依赖
uv sync
echo "Python 依赖安装完成"
echo ""

# 2. Node.js 依赖（npm）
echo "--- 安装 Node.js 依赖 (npm) ---"
if ! command -v npm &> /dev/null; then
    echo "错误: 未找到 npm，请先安装 Node.js"
    exit 1
fi
npm install
echo "Node.js 依赖安装完成"
echo ""

echo "=== 所有依赖安装完成 ==="
