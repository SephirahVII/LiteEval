# Agent sandbox image: headless coding agents for the liteeval.agent/v1 Harness.
# Contains everything the Target and the command Judge need; built and pushed
# to a local registry by images/agent-sandbox/build.sh so benchmark packages
# can pin an immutable repo digest.
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python-is-python3 git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG CLAUDE_CODE_VERSION=latest
ARG DSH_VERSION=0.2.1-rc.5
RUN npm install -g @anthropic-ai/claude-code@$CLAUDE_CODE_VERSION @deepseek-ai/dsh@$DSH_VERSION \
 && npm cache clean --force

# dsh bundles node-addon-require-builtin; npm does not install optional
# dependencies of bundled packages, so the linux/arm64 prebuilt addon is
# missing after a global install. Place it where the custom loader expects it.
RUN set -eux; \
    target=/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/node-addon-require-builtin; \
    npm pack node-addon-require-builtin-linux-arm64-gnu@0.1.9 --pack-destination /tmp; \
    tar xzf /tmp/node-addon-require-builtin-linux-arm64-gnu-0.1.9.tgz -C /tmp; \
    mkdir -p "$target/build/napi/napi-v9-linux-arm64-gnu"; \
    cp /tmp/package/prebuilt/linux-arm64-gnu-napi-v9.node "$target/build/napi/napi-v9-linux-arm64-gnu/require_builtin.node"

# The platform runs containers with a read-only rootfs, dropped capabilities,
# a host-mapped uid and a small tmpfs at /tmp. Everything above must therefore
# be world-readable; Harnesses relocate HOME / DSH_HOME to /tmp at runtime.
RUN chmod -R a+rX /usr/local/lib/node_modules
