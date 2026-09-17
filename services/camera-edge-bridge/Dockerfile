# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d

ARG RUST_IMAGE=rust:1.96.0-slim-bookworm@sha256:4732ca96fd086cb9be682050c3f0176288eebaac2b80aa2bcefccfaf198e1950
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

FROM ${RUST_IMAGE} AS dependencies

WORKDIR /dependency-build

RUN rustup component add clippy rustfmt

COPY services/camera-edge-bridge/Cargo.toml services/camera-edge-bridge/Cargo.lock ./

RUN install -d src \
    && printf 'pub fn dependency_cache() {}\n' > src/lib.rs \
    && printf 'fn main() {}\n' > src/main.rs

RUN --mount=type=cache,id=scrap-camera-edge-bridge-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=scrap-camera-edge-bridge-cargo-target-arm64,target=/dependency-build/target,sharing=locked \
    cargo clippy --locked --all-targets --all-features --no-deps -- -D warnings \
    && cargo test --locked --all-targets --all-features --no-run \
    && cargo build --locked --release \
    && cargo clean -p scrap-synthetic-camera-bridge \
    && cargo clean --release -p scrap-synthetic-camera-bridge \
    && install -d /cargo-cache/registry /cargo-cache/target \
    && { rustc -Vv; sha256sum Cargo.toml Cargo.lock; } \
       | sha256sum | cut -d ' ' -f 1 > /cargo-cache/key \
    && cp /cargo-cache/key /usr/local/cargo/registry/.dependency-cache-key \
    && cp /cargo-cache/key target/.dependency-cache-key \
    && cp -a /usr/local/cargo/registry/. /cargo-cache/registry/ \
    && cp -a target/. /cargo-cache/target/

FROM dependencies AS source

WORKDIR /work

COPY services/camera-edge-bridge/Cargo.toml services/camera-edge-bridge/Cargo.lock ./
COPY services/camera-edge-bridge/src/ src/

FROM source AS checks

RUN --mount=type=cache,id=scrap-camera-edge-bridge-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=scrap-camera-edge-bridge-cargo-target-arm64,target=/work/target,sharing=locked \
    if ! cmp -s /cargo-cache/key /usr/local/cargo/registry/.dependency-cache-key; then \
        cp -a /cargo-cache/registry/. /usr/local/cargo/registry/; \
    fi \
    && if ! cmp -s /cargo-cache/key target/.dependency-cache-key; then \
        cp -a /cargo-cache/target/. target/; \
    fi \
    && cargo clean -p scrap-synthetic-camera-bridge \
    && cargo fmt --all -- --check \
    && cargo clippy --locked --all-targets --all-features -- -D warnings \
    && cargo test --locked --all-targets --all-features

FROM checks AS release

RUN --mount=type=cache,id=scrap-camera-edge-bridge-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=scrap-camera-edge-bridge-cargo-target-arm64,target=/work/target,sharing=locked \
    if ! cmp -s /cargo-cache/key /usr/local/cargo/registry/.dependency-cache-key; then \
        cp -a /cargo-cache/registry/. /usr/local/cargo/registry/; \
    fi \
    && if ! cmp -s /cargo-cache/key target/.dependency-cache-key; then \
        cp -a /cargo-cache/target/. target/; \
    fi \
    && cargo clean --release -p scrap-synthetic-camera-bridge \
    && cargo build --locked --release \
    && install -d /artifacts /notices \
    && install target/release/scrap-synthetic-camera-bridge \
       /artifacts/scrap-synthetic-camera-bridge \
    && cp "$(rustc --print sysroot)/share/doc/rust/COPYRIGHT.html" \
       /notices/RUST_COPYRIGHT.html

FROM release AS test

FROM ${RUNTIME_IMAGE} AS runtime

ARG BUILD_REVISION=unknown
ARG BUILD_VERSION=0.3.1

LABEL org.opencontainers.image.description="Bounded WebSocket to V4L2 bridge for the Scrap synthetic camera" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.source="https://github.com/ajin-scrap-monitoring/scrap-monitoring-simulator" \
      org.opencontainers.image.title="Scrap Monitoring Simulator Camera Edge Bridge" \
      org.opencontainers.image.version="${BUILD_VERSION}"

COPY --from=test /artifacts/scrap-synthetic-camera-bridge /usr/local/bin/scrap-synthetic-camera-bridge
COPY services/camera-edge-bridge/licenses/THIRD_PARTY_NOTICES.html /usr/share/doc/scrap-synthetic-camera-bridge/THIRD_PARTY_NOTICES.html
COPY --from=test /notices/RUST_COPYRIGHT.html /usr/share/doc/scrap-synthetic-camera-bridge/RUST_COPYRIGHT.html

USER 10001:10001
STOPSIGNAL SIGTERM

ENTRYPOINT ["/usr/local/bin/scrap-synthetic-camera-bridge"]
