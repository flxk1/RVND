# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
# Rvnd server + console bridge. Build from the repository root:
#   docker build -f deploy/Dockerfile -t rvnd .
# The bridge leaves loopback only with RVND_BIND set AND a declared
# WORKSPACE_PRINCIPAL_HEADER (fail-closed) - compose.yml pairs both.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

RUN groupadd -r rvnd \
    && useradd -r -g rvnd -s /usr/sbin/nologin rvnd-broker \
    && useradd -r -g rvnd -s /usr/sbin/nologin rvnd-egress \
    && useradd -r -g rvnd -s /usr/sbin/nologin rvnd-agent

WORKDIR /opt/rvnd
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY LICENSES ./LICENSES
COPY server/src ./server/src
COPY app ./app
RUN pip install --no-cache-dir . \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data/keys /data/logs /workspaces \
    && chown -R rvnd-broker:rvnd /data /workspaces

# governed state lives on the volume, never in the image
ENV RVND_BIND=0.0.0.0

VOLUME ["/data", "/workspaces"]
EXPOSE 8799

USER rvnd-broker
CMD ["python", "app/serve.py", "8799", "--no-open"]
