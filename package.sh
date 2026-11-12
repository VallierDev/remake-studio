#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "请先提交源码改动，再打包。"
    exit 1
fi
mkdir -p dist
revision=$(git rev-parse --short HEAD)
git archive --format=zip --prefix=remake-studio/ --output="dist/remake-studio-$revision.zip" HEAD
echo "$PWD/dist/remake-studio-$revision.zip"
