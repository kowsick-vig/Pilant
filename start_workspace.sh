#!/bin/sh
set -eu
workspace_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$workspace_dir"
if [ -x "$workspace_dir/.react-venv/bin/python" ]; then
    exec "$workspace_dir/.react-venv/bin/python" -B workspace_api.py
elif [ -x "$workspace_dir/venv/bin/python" ]; then
    exec "$workspace_dir/venv/bin/python" -B workspace_api.py
else
    exec python3 -B workspace_api.py
fi
