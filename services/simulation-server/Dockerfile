# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d

ARG RUST_IMAGE=rust:1.96.0-slim-bookworm@sha256:4732ca96fd086cb9be682050c3f0176288eebaac2b80aa2bcefccfaf198e1950
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

FROM ${RUST_IMAGE} AS source

WORKDIR /work

COPY services/simulation-server/Cargo.toml services/simulation-server/Cargo.lock \
    services/simulation-server/rust-toolchain.toml ./
COPY services/simulation-server/src/ src/
COPY services/simulation-server/tests/ tests/
COPY services/simulation-server/config/ config/
COPY contracts/ contracts/

FROM source AS test

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cmake \
        g++ \
        make \
    && rm -rf /var/lib/apt/lists/*

COPY services/simulation-server/sdk-acceptance/ sdk-acceptance/

RUN cargo fmt --all -- --check \
    && cargo clippy --locked --all-targets --all-features -- -D warnings \
    && cargo test --locked --all-targets --all-features \
    && cargo build --locked --release

RUN cmake -S sdk-acceptance -B sdk-build -DCMAKE_BUILD_TYPE=Release -Wno-dev \
    && cmake --build sdk-build --parallel \
    && sdk-acceptance/run.sh \
        /work/target/release/scrap-monitoring-simulation-server \
        /work/sdk-build/sdk-acceptance

FROM source AS builder

RUN cargo build --locked --release \
    && install -d /notices \
    && cp "$(rustc --print sysroot)/share/doc/rust/COPYRIGHT.html" \
       /notices/RUST_COPYRIGHT.html

FROM ${RUNTIME_IMAGE} AS runtime

RUN groupadd --gid 10001 simulator \
    && useradd --uid 10001 --gid simulator --no-create-home --home-dir /tmp simulator

COPY --from=builder /work/target/release/scrap-monitoring-simulation-server \
    /usr/local/bin/scrap-monitoring-simulation-server
COPY --chown=simulator:simulator services/simulation-server/config/ /app/config/
COPY services/simulation-server/licenses/THIRD_PARTY_NOTICES.html \
    /usr/share/doc/scrap-monitoring-simulation-server/THIRD_PARTY_NOTICES.html
COPY --from=builder /notices/RUST_COPYRIGHT.html \
    /usr/share/doc/scrap-monitoring-simulation-server/RUST_COPYRIGHT.html

ARG BUILD_REVISION=unknown
ARG BUILD_VERSION=0.1.0

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
