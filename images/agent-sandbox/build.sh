#!/usr/bin/env bash
# Build the agent sandbox image and pin it by repo digest.
#
# The benchmark schema requires docker images as `repo@sha256:<digest>`. Repo
# digests only exist for images pushed to a registry, so this script maintains
# a local registry container (localhost:5000), pushes the image there and
# prints the pinned reference for dataset.toml.
set -euo pipefail

IMAGE_NAME="liteeval/agent-sandbox"
IMAGE_TAG="1.0.0"
REGISTRY="localhost:5000"

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not reachable" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx liteeval-registry; then
  docker rm -f liteeval-registry >/dev/null 2>&1 || true
  docker run -d --name liteeval-registry -p 5000:5000 --restart unless-stopped registry:2 >/dev/null
fi

root="$(cd "$(dirname "$0")" && pwd)"
docker build --platform linux/arm64 -t "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG" "$root"
docker push "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG" >/dev/null

pinned="$(docker inspect --format '{{index .RepoDigests 0}}' "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG")"
echo "$pinned"
echo "Pin this reference in dataset.toml environment_profiles: image = \"$pinned\"" >&2
