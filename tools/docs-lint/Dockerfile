# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d

ARG PYTHON_IMAGE=python@sha256:9ab8d9c8514b44f90cf0029dd42fdd7e9e211e639c8b995304cc04568dee900f
ARG UV_IMAGE=ghcr.io/astral-sh/uv@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS check

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /repo

COPY README.md README.md
COPY docs/ docs/
COPY services/visualizer/THIRD_PARTY_NOTICES.md services/visualizer/THIRD_PARTY_NOTICES.md

RUN --mount=type=cache,id=scrap-docs-lint-uv,target=/root/.cache/uv,sharing=locked \
    uv tool run --from rumdl==0.2.70 rumdl \
        check --no-config --no-cache --disable MD013 \
        README.md docs services/visualizer/THIRD_PARTY_NOTICES.md
