# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d

ARG RUST_IMAGE=rust:1.96.0-slim-bookworm@sha256:4732ca96fd086cb9be682050c3f0176288eebaac2b80aa2bcefccfaf198e1950
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

FROM ${RUST_IMAGE} AS source

WORKDIR /work
COPY services/camera-edge-bridge/Cargo.toml services/camera-edge-bridge/Cargo.lock ./
COPY services/camera-edge-bridge/src/ src/

FROM source AS test

RUN rustup component add clippy rustfmt \
    && cargo fmt --all -- --check \
    && cargo clippy --locked --all-targets --all-features -- -D warnings \
    && cargo test --locked --all-targets --all-features \
    && cargo build --locked --release

FROM source AS builder

RUN cargo build --locked --release \
    && install -d /notices \
    && cp "$(rustc --print sysroot)/share/doc/rust/COPYRIGHT.html" \
       /notices/RUST_COPYRIGHT.html

FROM ${RUNTIME_IMAGE} AS runtime

ARG BUILD_REVISION=unknown
ARG BUILD_VERSION=0.1.0

LABEL org.opencontainers.image.description="Bounded WebSocket to V4L2 bridge for the Scrap synthetic camera" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.source="https://github.com/ajin-scrap-monitoring/scrap-monitoring-simulator" \
      org.opencontainers.image.title="Scrap Monitoring Simulator Camera Edge Bridge" \
      org.opencontainers.image.version="${BUILD_VERSION}"

COPY --from=builder /work/target/release/scrap-synthetic-camera-bridge /usr/local/bin/scrap-synthetic-camera-bridge
COPY services/camera-edge-bridge/licenses/THIRD_PARTY_NOTICES.html /usr/share/doc/scrap-synthetic-camera-bridge/THIRD_PARTY_NOTICES.html
COPY --from=builder /notices/RUST_COPYRIGHT.html /usr/share/doc/scrap-synthetic-camera-bridge/RUST_COPYRIGHT.html

USER 10001:10001
STOPSIGNAL SIGTERM

ENTRYPOINT ["/usr/local/bin/scrap-synthetic-camera-bridge"]
