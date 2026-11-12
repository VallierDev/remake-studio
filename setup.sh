#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p vendor

if [ "${1:-}" = "--all" ]; then
    sh "$0"
    sh "$0" --flow2api
    exit 0
fi

if [ "${1:-}" = "--flow2api" ]; then
    directory=vendor/flow2api
    revision=2cf700d1c6f36348b37405dbc0c9a2d5397338e4
    if [ ! -d "$directory/.git" ]; then
        git clone --depth 1 https://github.com/TheSmallHanCat/flow2api.git "$directory"
    fi
    if [ "$(git -C "$directory" rev-parse HEAD)" != "$revision" ]; then
        git -C "$directory" fetch --depth 1 origin "$revision"
        git -C "$directory" checkout --detach FETCH_HEAD
    fi
    [ -x .flow2api-venv/bin/python ] || uv venv --python 3.12 .flow2api-venv
    uv pip install --python .flow2api-venv/bin/python -r "$directory/requirements.txt"
    exit 0
fi

checkout() {
    directory=$1
    url=$2
    revision=$3
    shift 3
    if [ ! -d "$directory/.git" ]; then
        git clone --depth 1 "$@" "$url" "$directory"
    fi
    if [ "$(git -C "$directory" rev-parse HEAD)" != "$revision" ]; then
        git -C "$directory" fetch --depth 1 origin "$revision"
        git -C "$directory" checkout --detach FETCH_HEAD
    fi
}

checkout vendor/rea-video-sparse https://github.com/JuneYaooo/rea-video.git \
    3669923645e3953632e7406a57fb883e0ba8a6e0 --filter=blob:none --sparse
git -C vendor/rea-video-sparse sparse-checkout set rea_video
checkout vendor/tiktok-viral-ai-automation https://github.com/abuzar561/tiktok-viral-ai-automation.git \
    2ad6fd6cf04a86627da153a4445c227151e7b2f3

[ -x .venv/bin/python ] || uv venv --python 3.13 .venv
[ -x .scout-venv/bin/python ] || uv venv --python 3.13 .scout-venv
uv pip install --python .venv/bin/python './vendor/rea-video-sparse[ocr,asr]' -r requirements.txt
uv pip install --python .scout-venv/bin/python -r vendor/tiktok-viral-ai-automation/requirements.txt
