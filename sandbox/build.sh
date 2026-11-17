#!/bin/sh
# Build image sandbox (Linux): ./sandbox/build.sh [mep-sandbox:latest]
set -e
TAG="${1:-mep-sandbox:latest}"
DIR="$(cd "$(dirname "$0")" && pwd)"
docker build -f "$DIR/image/Dockerfile" -t "$TAG" "$DIR"
