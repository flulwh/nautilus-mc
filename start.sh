#!/bin/sh
# =====================================================================
#  Nautilus 一键启动（Linux / macOS）
#  首次运行会自动建 .venv 并安装依赖；之后直接启动。
# =====================================================================
set -eu
cd "$(dirname "$0")"

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "[x] 没找到 Python 3.11+"
    echo "    macOS:   brew install python@3.12"
    echo "    Debian:  sudo apt install python3 python3-venv"
    echo "    RHEL:    sudo dnf install python3"
    exit 1
fi

# 用 exec 替换当前 shell，这样 Ctrl+C 直接落在 start.py 上。
exec "$PY" -u start.py "$@"
