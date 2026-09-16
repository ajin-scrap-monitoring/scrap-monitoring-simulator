# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d

ARG RUST_IMAGE=rust:1.96.0-slim-bookworm@sha256:4732ca96fd086cb9be682050c3f0176288eebaac2b80aa2bcefccfaf198e1950
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

FROM ${RUST_IMAGE} AS dependencies

WORKDIR /dependency-build

COPY services/simulation-server/Cargo.toml services/simulation-server/Cargo.lock \
    services/simulation-server/rust-toolchain.toml ./

RUN install -d src tests \
    && printf 'pub fn dependency_cache() {}\n' > src/lib.rs \
    && printf 'fn main() {}\n' > src/main.rs \
    && printf '#[test]\nfn dependency_cache() {}\n' > tests/dependency_cache.rs

RUN --mount=type=cache,id=scrap-simulation-server-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=scrap-simulation-server-cargo-target-amd64,target=/dependency-build/target,sharing=locked \
    cargo clippy --locked --all-targets --all-features --no-deps -- -D warnings \
    && cargo test --locked --all-targets --all-features --no-run \
    && cargo build --locked --release \
    && cargo clean -p scrap-monitoring-simulation-server \
    && cargo clean --release -p scrap-monitoring-simulation-server \
    && install -d /cargo-cache/registry /cargo-cache/target \
    && { rustc -Vv; sha256sum Cargo.toml Cargo.lock rust-toolchain.toml; } \
       | sha256sum | cut -d ' ' -f 1 > /cargo-cache/key \
    && cp /cargo-cache/key /usr/local/cargo/registry/.dependency-cache-key \
    && cp /cargo-cache/key target/.dependency-cache-key \
    && cp -a /usr/local/cargo/registry/. /cargo-cache/registry/ \
    && cp -a target/. /cargo-cache/target/

FROM dependencies AS source

WORKDIR /work

COPY services/simulation-server/Cargo.toml services/simulation-server/Cargo.lock \
    services/simulation-server/rust-toolchain.toml ./
COPY services/simulation-server/src/ src/
COPY services/simulation-server/tests/ tests/
COPY services/simulation-server/config/ config/
COPY contracts/ contracts/

FROM source AS checks

RUN --mount=type=cache,id=scrap-simulation-server-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=scrap-simulation-server-cargo-target-amd64,target=/work/target,sharing=locked \
    if ! cmp -s /cargo-cache/key /usr/local/cargo/registry/.dependency-cache-key; then \
        cp -a /cargo-cache/registry/. /usr/local/cargo/registry/; \
    fi \
    && if ! cmp -s /cargo-cache/key target/.dependency-cache-key; then \
        cp -a /cargo-cache/target/. target/; \
    fi \
    && cargo clean -p scrap-monitoring-simulation-server \
    && cargo fmt --all -- --check \
    && cargo clippy --locked --all-targets --all-features -- -D warnings \
    && cargo test --locked --all-targets --all-features

FROM checks AS release

RUN --mount=type=cache,id=scrap-simulation-server-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=scrap-simulation-server-cargo-target-amd64,target=/work/target,sharing=locked \
    if ! cmp -s /cargo-cache/key /usr/local/cargo/registry/.dependency-cache-key; then \
        cp -a /cargo-cache/registry/. /usr/local/cargo/registry/; \
    fi \
    && if ! cmp -s /cargo-cache/key target/.dependency-cache-key; then \
        cp -a /cargo-cache/target/. target/; \
    fi \
    && cargo clean --release -p scrap-monitoring-simulation-server \
    && cargo build --locked --release \
    && install -d /artifacts /notices \
    && install target/release/scrap-monitoring-simulation-server \
       /artifacts/scrap-monitoring-simulation-server \
    && cp "$(rustc --print sysroot)/share/doc/rust/COPYRIGHT.html" \
       /notices/RUST_COPYRIGHT.html

FROM release AS test

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cmake \
        g++ \
        make \
    && rm -rf /var/lib/apt/lists/*

COPY services/simulation-server/sdk-acceptance/ sdk-acceptance/

RUN cmake -S sdk-acceptance -B sdk-build -DCMAKE_BUILD_TYPE=Release -Wno-dev \
    && cmake --build sdk-build --parallel \
    && sdk-acceptance/run.sh \
        /artifacts/scrap-monitoring-simulation-server \
        /work/sdk-build/sdk-acceptance

FROM ${RUNTIME_IMAGE} AS runtime

RUN groupadd --gid 10001 simulator \
    && useradd --uid 10001 --gid simulator --no-create-home --home-dir /tmp simulator

COPY --from=test /artifacts/scrap-monitoring-simulation-server \
    /usr/local/bin/scrap-monitoring-simulation-server
COPY --chown=simulator:simulator services/simulation-server/config/ /app/config/
COPY services/simulation-server/licenses/THIRD_PARTY_NOTICES.html \
    /usr/share/doc/scrap-monitoring-simulation-server/THIRD_PARTY_NOTICES.html
COPY --from=test /notices/RUST_COPYRIGHT.html \
    /usr/share/doc/scrap-monitoring-simulation-server/RUST_COPYRIGHT.html

ARG BUILD_REVISION=unknown
ARG BUILD_VERSION=0.2.1

LABEL org.opencontainers.image.description="Canonical scene and RPLIDAR S2E simulation server" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.source="https://github.com/ajin-scrap-monitoring/scrap-monitoring-simulator" \
      org.opencontainers.image.title="Scrap Monitoring Simulation Server" \
      org.opencontainers.image.version="${BUILD_VERSION}"

USER 10001:10001
WORKDIR /app
STOPSIGNAL SIGTERM

EXPOSE 8089/udp 8090/udp

ENTRYPOINT ["/usr/local/bin/scrap-monitoring-simulation-server"]
CMD ["run"]
