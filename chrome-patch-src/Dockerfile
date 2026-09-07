# syntax=docker/dockerfile:1.7

ARG CHROMIUM_VERSION=151.0.7922.169
ARG SANDBOX_FIX_COMMIT=4298968d02fa7a24dccc65b03071af84c5418c38
ARG DEPOT_TOOLS_REVISION=547d7e12fe104305c7de797d5a2b4155914ad362
ARG DEBIAN_IMAGE=debian:bookworm-slim
ARG PYTHON_IMAGE=python:3.13.15-slim-bookworm

FROM ${PYTHON_IMAGE} AS chromium-builder
ARG CHROMIUM_VERSION
ARG SANDBOX_FIX_COMMIT
ARG DEPOT_TOOLS_REVISION
ARG BUILD_JOBS=8
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/usr/lib/python3/dist-packages \
    CHROMIUM_VERSION=${CHROMIUM_VERSION} \
    SANDBOX_FIX_COMMIT=${SANDBOX_FIX_COMMIT} \
    DEPOT_TOOLS_REVISION=${DEPOT_TOOLS_REVISION} \
    BUILD_JOBS=${BUILD_JOBS}
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl file git lsb-release python3 python3-httplib2 sudo xz-utils \
 && rm -rf /var/lib/apt/lists/*
COPY chromium/verify_source_fix.py chromium/package_runtime.py chromium/build_chromium.sh /build-tools/
RUN --mount=type=cache,id=chrome-patch-depot-tools,target=/opt/depot_tools,sharing=locked \
    --mount=type=cache,id=chrome-patch-chromium-source,target=/src/chromium,sharing=locked \
    --mount=type=cache,id=chrome-patch-downloads,target=/root/.cache,sharing=locked \
    /bin/sh /build-tools/build_chromium.sh

FROM ${PYTHON_IMAGE} AS scraper-runtime
ARG CHROMIUM_VERSION
ARG SANDBOX_FIX_COMMIT
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/assignment8:/app/capstone \
    HOME=/home/scraper \
    XDG_CACHE_HOME=/home/scraper/.cache \
    XDG_CONFIG_HOME=/home/scraper/.config \
    CHROMIUM_BINARY=/opt/chromium/chrome \
    CHROMEDRIVER_PATH=/opt/chromium/chromedriver \
    APPROVED_CHROMIUM_VERSION=${CHROMIUM_VERSION} \
    REQUIRED_SANDBOX_FIX_COMMIT=${SANDBOX_FIX_COMMIT}
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 \
      libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libexpat1 libgbm1 \
      libglib2.0-0 libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libudev1 \
      libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
      libxkbcommon0 libxrandr2 xdg-utils \
 && rm -rf /var/lib/apt/lists/*
COPY --from=chromium-builder /chromium-runtime/ /opt/chromium/
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 scraper \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin scraper \
 && mkdir -p /results/assignment8 /results/capstone /home/scraper/.cache /home/scraper/.config \
 && chown -R 10001:10001 /results /home/scraper \
 && chmod -R go-w /opt/chromium
COPY --chown=10001:10001 . /app
COPY docker/chromium-policy.json /etc/chromium/policies/managed/scraper.json
USER 10001:10001
ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
CMD ["python", "-m", "common.sandbox_probe"]
