#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import fastapi,uvicorn' >/dev/null 2>&1; then
    echo "首次使用请先运行 ./setup.sh --all"
    exit 1
fi
if curl -fsS --max-time 1 http://127.0.0.1:18782/api/system >/dev/null 2>&1; then
    .venv/bin/python -c "import webbrowser; webbrowser.open('http://127.0.0.1:18782/')"
else
    exec .venv/bin/python workbench.py --open
fi
