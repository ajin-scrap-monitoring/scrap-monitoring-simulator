# 의존성

## 직접 의존성

Repository가 직접 선택한 의존성은 다음 5개 묶음, 52개 표 항목이다. Service별 표에서 같은
toolchain을 각각 사용하는 경우에는 각 service 항목으로 센다.

| 묶음 | 항목 수 |
| --- | --- |
| Simulation server | 11 |
| Visualizer runtime | 17 |
| Camera edge bridge | 6 |
| Package build와 검증 | 9 |
| GitHub Actions와 image build | 9 |

### Simulation server

| 의존성 | 버전 | 목적 | 공식 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| Rust | 1.96.0 | AMD64 server build, format과 lint | [Rust](https://github.com/rust-lang/rust) | MIT OR Apache-2.0 |
| `clap` | 4.6.6 | Runtime command와 환경 변수 parsing | [docs.rs](https://docs.rs/crate/clap/4.6.6) | MIT OR Apache-2.0 |
| `num-bigint` | 0.4.8 | Sample 및 회전 비율의 정수 계산 | [docs.rs](https://docs.rs/crate/num-bigint/0.4.8) | MIT OR Apache-2.0 |
| `serde` | 1.0.229 | 설정과 scene 자료형 직렬화 | [docs.rs](https://docs.rs/crate/serde/1.0.229) | MIT OR Apache-2.0 |
| `serde_json` | 1.0.151 | Strict JSON parsing과 scene encoding | [docs.rs](https://docs.rs/crate/serde_json/1.0.151) | MIT OR Apache-2.0 |
| `sha2` | 0.11.0 | 입력 fingerprint와 sensor serial 생성 | [docs.rs](https://docs.rs/crate/sha2/0.11.0) | MIT OR Apache-2.0 |
| `thiserror` | 2.0.20 | 구성 및 runtime 오류 분류 | [docs.rs](https://docs.rs/crate/thiserror/2.0.20) | MIT OR Apache-2.0 |
| `tokio` | 1.53.1 | UDP, TCP, signal과 비동기 task runtime | [docs.rs](https://docs.rs/crate/tokio/1.53.1) | MIT |
| `uuid` | 1.26.1 | Simulation run identifier 생성 | [docs.rs](https://docs.rs/crate/uuid/1.26.1) | Apache-2.0 OR MIT |
| `tempfile` | 3.27.0 | 격리된 filesystem test | [docs.rs](https://docs.rs/crate/tempfile/3.27.0) | MIT OR Apache-2.0 |
| RPLIDAR SDK | 2.1.0, commit `99478e5fb90de3b4a6db0080acacd373f8b36869` | 실제 SDK의 UDP 호환 수락 검사 | [Slamtec](https://github.com/Slamtec/rplidar_sdk) | BSD-2-Clause |

RPLIDAR SDK는 test stage에서만 build하며 Simulation server runtime image에는 포함하지 않는다.

### Visualizer runtime

| 의존성 | 버전 | 목적 | 공식 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| Python | 3.14.7 | Visualizer 실행 | [Python](https://www.python.org/downloads/release/python-3147/) | PSF-2.0 |
| FastAPI | 0.141.1 | HTTP와 WebSocket application | [PyPI](https://pypi.org/project/fastapi/0.141.1/) | MIT |
| jsonschema | 4.26.0 | Versioned scene 계약 검증 | [PyPI](https://pypi.org/project/jsonschema/4.26.0/) | MIT |
| NumPy | 2.5.3 | 격자, mesh와 image 효과 수치 배열 | [PyPI](https://pypi.org/project/numpy/2.5.3/) | BSD-3-Clause |
| Pillow | 12.3.0 | JPEG memory encoding | [PyPI](https://pypi.org/project/pillow/12.3.0/) | MIT-CMU |
| PyVista | 0.49.0 | synthetic camera VTK scene과 off-screen rendering 경계 | [PyPI](https://pypi.org/project/pyvista/0.49.0/) | MIT |
| Uvicorn | 0.52.4 | HTTP와 WebSocket ASGI server | [PyPI](https://pypi.org/project/uvicorn/0.52.4/) | BSD-3-Clause |
| websockets | 17.1 | Uvicorn WebSocket protocol runtime | [PyPI](https://pypi.org/project/websockets/17.1/) | BSD-3-Clause |
| VTK | 9.7.0 | synthetic camera 원근 3D rendering | [PyPI](https://pypi.org/project/vtk/9.7.0/) | BSD-3-Clause |
| Mesa `libosmesa6` | 22.3.6-1+deb12u2 | CPU off-screen OpenGL context | [Debian](https://packages.debian.org/bookworm/libosmesa6) | MIT 및 구성 요소별 라이선스 |
| GLVND `libegl1` | 1.6.0-1 | EGL vendor-neutral dispatch | [Debian](https://packages.debian.org/bookworm/libegl1) | MIT 및 구성 요소별 라이선스 |
| GLVND `libgl1` | 1.6.0-1 | OpenGL vendor-neutral dispatch | [Debian](https://packages.debian.org/bookworm/libgl1) | MIT 및 구성 요소별 라이선스 |
| GLVND `libglvnd0` | 1.6.0-1 | EGL과 OpenGL dispatch runtime | [Debian](https://packages.debian.org/bookworm/libglvnd0) | MIT 및 구성 요소별 라이선스 |
| GLVND `libopengl0` | 1.6.0-1 | OpenGL API dispatch runtime | [Debian](https://packages.debian.org/bookworm/libopengl0) | MIT 및 구성 요소별 라이선스 |
| Node.js | 24 | Browser bundle build | [Node.js](https://nodejs.org/) | MIT |
| Three.js | 0.180.0 | Browser WebGL 3D model | [Three.js](https://github.com/mrdoob/three.js) | MIT |
| TypeScript | 5.9.2 | Browser protocol과 rendering source compile | [TypeScript](https://github.com/microsoft/TypeScript) | Apache-2.0 |

### Camera edge bridge

| 의존성 | 버전 | 목적 | 공식 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| Rust | 1.96.0 | Linux ARM64 bridge build, format과 lint | [Rust](https://github.com/rust-lang/rust) | MIT OR Apache-2.0 |
| `libc` | 0.2.175 | Video4Linux2 ioctl과 file descriptor 경계 | [crates.io](https://crates.io/crates/libc/0.2.175) | Apache-2.0 OR MIT |
| `serde` | 1.0.228 | Stream descriptor 역직렬화 | [crates.io](https://crates.io/crates/serde/1.0.228) | Apache-2.0 OR MIT |
| `serde_json` | 1.0.145 | Strict JSON descriptor parsing | [crates.io](https://crates.io/crates/serde_json/1.0.145) | Apache-2.0 OR MIT |
| `signal-hook` | 0.3.18 | SIGINT와 SIGTERM 종료 처리 | [crates.io](https://crates.io/crates/signal-hook/0.3.18) | Apache-2.0 OR MIT |
| `tungstenite` | 0.28.0 | Plain WebSocket client와 message 제한 | [crates.io](https://crates.io/crates/tungstenite/0.28.0) | Apache-2.0 OR MIT |

Tungstenite는 default feature와 TLS feature를 끄고 handshake feature만 사용한다.

### Package build와 검증

| 의존성 | 버전 | 목적 | 공식 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| uv 및 uv_build | 0.12.13 | Python 환경, lock file과 package build | [Astral](https://github.com/astral-sh/uv) | Apache-2.0 OR MIT |
| mypy | 2.3.1 | Python 정적 type 검사 | [PyPI](https://pypi.org/project/mypy/2.3.1/) | MIT |
| pytest | 9.1.1 | Python 자동 test | [PyPI](https://pypi.org/project/pytest/9.1.1/) | MIT |
| Ruff | 0.16.6 | Python format과 lint | [PyPI](https://pypi.org/project/ruff/0.16.6/) | MIT |
| rumdl | 0.2.70 | Markdown 검사 | [PyPI](https://pypi.org/project/rumdl/0.2.70/) | MIT |
| `types-jsonschema` | 4.26.0.20260518 | jsonschema 정적 type 정보 | [typeshed](https://github.com/python/typeshed) | Apache-2.0 |
| `cargo-about` | 0.9.2 | Rust crate 라이선스 검사와 notice 생성 | [crates.io](https://crates.io/crates/cargo-about/0.9.2) | Apache-2.0 OR MIT |
| actionlint | 1.7.12 | GitHub Actions workflow 문법 검사 | [GitHub](https://github.com/rhysd/actionlint) | MIT |
| `jq` | 1.6 이상 | Compose JSON 구조와 LiDAR port 계약 검사 | [jqlang](https://github.com/jqlang/jq) | MIT |

### GitHub Actions와 image build

| 의존성 | 버전 | 목적 | 공식 출처 | 라이선스 |
| --- | --- | --- | --- | --- |
| `actions/checkout` | 7.0.1 | Repository와 tag 이력 checkout | [GitHub](https://github.com/actions/checkout) | MIT |
| `docker/setup-buildx-action` | 4.4.1 | Multi-platform image builder 구성 | [GitHub](https://github.com/docker/setup-buildx-action) | Apache-2.0 |
| Docker Buildx | 0.37.1 | 고정 BuildKit builder와 image build 제어 | [GitHub](https://github.com/docker/buildx) | Apache-2.0 |
| Moby BuildKit | 0.33.0 | Open Container Initiative image build | [GitHub](https://github.com/moby/buildkit) | Apache-2.0 |
| `docker/bake-action` | 7.4.0 | Service별 병렬 Buildx Bake 실행과 cache 연결 | [GitHub](https://github.com/docker/bake-action) | Apache-2.0 |
| `docker/login-action` | 4.6.0 | GitHub Container Registry 인증 | [GitHub](https://github.com/docker/login-action) | Apache-2.0 |
| `docker/build-push-action` | 7.4.0 | Image, Software Bill of Materials와 provenance 게시 | [GitHub](https://github.com/docker/build-push-action) | Apache-2.0 |
| `softprops/action-gh-release` | 3.0.3 | Image inventory를 포함한 GitHub Release 게시 | [GitHub](https://github.com/softprops/action-gh-release) | MIT |
| `docker/dockerfile` | 1.20 | 고정 Dockerfile frontend | [GitHub](https://github.com/moby/buildkit) | Apache-2.0 |

## 고정과 notice 경계

각 service는 재현 가능한 lock file과 digest로 고정한 Container base image를 사용한다.
Simulation server와 Camera edge bridge는 각각의 `Cargo.lock`, Visualizer는 `uv.lock`을
정본으로 사용한다. GitHub Actions는 workflow에서 commit hash로 고정한다.

| Service | Notice |
| --- | --- |
| Simulation server | `services/simulation-server/licenses/THIRD_PARTY_NOTICES.html` |
| Visualizer | `services/visualizer/licenses/THIRD_PARTY_NOTICES.html`와 Python `dist-info` |
| Camera edge bridge | `services/camera-edge-bridge/licenses/THIRD_PARTY_NOTICES.html` |

Rust image는 crate notice와 Rust toolchain `COPYRIGHT.html`을 runtime image에 포함한다.
Visualizer image는 Browser bundle의 Three.js notice, Python distribution notice와 Debian
package notice를 포함한다. Release workflow는 각 image에 Software Bill of Materials(SBOM)와
build provenance를 연결한다.

## Edge host 의존성

Edge host의 one-time 설정과 검증은 다음 4개 package 묶음을 사용한다. 이 package는 edge
Container image에 포함하지 않으며 설치 대상 Linux distribution의 package version과 license
metadata를 따른다.

| Package | 목적 |
| --- | --- |
| 현재 kernel header | `v4l2loopback` DKMS build |
| `v4l2loopback-dkms`, `v4l2loopback-utils` | Virtual camera kernel module과 제어 도구 |
| `v4l-utils` | Video4Linux2 format, FPS와 control 설정 |
| FFmpeg와 `ffprobe` | 실제 90 frame capture와 decode 검증 |
