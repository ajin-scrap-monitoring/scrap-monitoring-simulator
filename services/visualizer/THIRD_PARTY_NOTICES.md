# Third-party notices

Visualizer의 Python 직접 및 전이 의존성은 `uv.lock`에 고정한다. Python distribution의
license와 notice 원문은 runtime image의
`/workspace/visualizer/.venv/lib/python3.14/site-packages/` 아래 각 `dist-info` 디렉터리에
포함한다.

Debian package의 저작권과 license 원문은 runtime image의 `/usr/share/doc/` 아래에
포함한다. Release workflow는 Visualizer image에 Software Bill of Materials와 build
provenance를 연결한다.

Browser bundle의 Three.js notice는 runtime image의
`/workspace/visualizer/licenses/THIRD_PARTY_NOTICES.html`에 포함한다.
