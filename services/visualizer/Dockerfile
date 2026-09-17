# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d

ARG PYTHON_IMAGE=python@sha256:9ab8d9c8514b44f90cf0029dd42fdd7e9e211e639c8b995304cc04568dee900f
ARG UV_IMAGE=ghcr.io/astral-sh/uv@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6
ARG NODE_IMAGE=node@sha256:48abc13a19400ca3985071e287bd405a1d99306770eb81d61202fb6b65cf0b57

FROM ${UV_IMAGE} AS uv

FROM ${NODE_IMAGE} AS web-builder

WORKDIR /workspace/visualizer/web

COPY services/visualizer/web/package.json services/visualizer/web/package-lock.json ./

RUN --mount=type=cache,id=scrap-visualizer-npm,target=/root/.npm,sharing=locked \
    npm ci --include=dev

COPY services/visualizer/web/ ./

RUN npm test \
    && npm run build \
    && test -s dist/assets/vendor/three.module.js \
    && test -s dist/assets/vendor/three.core.js

FROM ${PYTHON_IMAGE} AS system

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libegl1=1.6.0-1 \
        libgl1=1.6.0-1 \
        libglvnd0=1.6.0-1 \
        libopengl0=1.6.0-1 \
        libosmesa6=22.3.6-1+deb12u2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

ENV HOME=/tmp \
    LIBGL_ALWAYS_SOFTWARE=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PATH=/workspace/visualizer/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYVISTA_OFF_SCREEN=true \
    UV_LINK_MODE=copy \
    VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /workspace/visualizer

FROM system AS runtime-dependencies

COPY services/visualizer/pyproject.toml services/visualizer/uv.lock ./

RUN --mount=type=cache,id=scrap-visualizer-uv,target=/tmp/cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-project

FROM runtime-dependencies AS dev-dependencies

RUN --mount=type=cache,id=scrap-visualizer-uv,target=/tmp/cache/uv,sharing=locked \
    uv sync --frozen --no-install-project

FROM runtime-dependencies AS builder

COPY services/visualizer/src/ src/
COPY contracts/ /workspace/contracts/

RUN --mount=type=cache,id=scrap-visualizer-uv,target=/tmp/cache/uv,sharing=locked \
    cmp ../contracts/scene/v2/definition.schema.json \
        src/scrap_monitoring_visualizer/contracts/schema/v2/definition.schema.json \
    && cmp ../contracts/scene/v2/segment.schema.json \
        src/scrap_monitoring_visualizer/contracts/schema/v2/segment.schema.json \
    && uv sync --frozen --no-dev --no-editable

FROM dev-dependencies AS test

COPY services/visualizer/src/ src/
COPY contracts/ /workspace/contracts/
COPY services/visualizer/tests/ tests/
COPY services/simulation-server/config/ /workspace/config/

RUN --mount=type=cache,id=scrap-visualizer-uv,target=/tmp/cache/uv,sharing=locked \
    cmp ../contracts/scene/v2/definition.schema.json \
        src/scrap_monitoring_visualizer/contracts/schema/v2/definition.schema.json \
    && cmp ../contracts/scene/v2/segment.schema.json \
        src/scrap_monitoring_visualizer/contracts/schema/v2/segment.schema.json \
    && uv sync --frozen --no-editable \
    && uv run ruff format --check src tests \
    && uv run ruff check src tests \
    && uv run mypy \
    && uv run pytest -q \
    && uv build --wheel --sdist \
    && touch /tmp/visualizer-tests-passed

FROM builder AS runtime-builder

COPY --from=test /tmp/visualizer-tests-passed /tmp/visualizer-tests-passed

FROM system AS runtime

RUN groupadd --gid 10001 visualizer \
    && useradd --uid 10001 --gid visualizer --no-create-home --home-dir /tmp visualizer

COPY --from=runtime-builder --chown=visualizer:visualizer \
    /workspace/visualizer/.venv /workspace/visualizer/.venv
COPY --from=runtime-builder --chown=visualizer:visualizer \
    /workspace/contracts /workspace/contracts
COPY --from=web-builder --chown=visualizer:visualizer \
    /workspace/visualizer/web/dist /workspace/visualizer/web/dist
COPY --chown=visualizer:visualizer services/visualizer/THIRD_PARTY_NOTICES.md \
    /workspace/visualizer/THIRD_PARTY_NOTICES.md
COPY --chown=visualizer:visualizer services/visualizer/licenses/THIRD_PARTY_NOTICES.html \
    /workspace/visualizer/licenses/THIRD_PARTY_NOTICES.html

ARG BUILD_REVISION=unknown
ARG BUILD_VERSION=0.4.0

LABEL org.opencontainers.image.description="Canonical scene browser and synthetic camera renderer" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.source="https://github.com/ajin-scrap-monitoring/scrap-monitoring-simulator" \
      org.opencontainers.image.title="Scrap Monitoring Simulator Visualizer" \
      org.opencontainers.image.version="${BUILD_VERSION}"

USER 10001:10001
STOPSIGNAL SIGTERM

EXPOSE 17000/tcp 18000/tcp

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18000/status', timeout=2).read()"]

ENTRYPOINT ["scrap-monitoring-visualizer"]
CMD ["--help"]
