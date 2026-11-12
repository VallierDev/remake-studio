#!/bin/sh
set -eu
cd "$(dirname "$0")"
export REMAKE_EDITION=finder
export PATH="$PWD/.venv/bin:$PATH"
exec ./start.sh
