variable "BUILD_REVISION" {
  default = "local"
}

variable "BUILD_VERSION" {
  default = "0.3.0"
}

variable "SCRAP_SIMULATOR_TEST_SERVER_IMAGE" {
  default = "scrap-monitoring-simulator-server:local"
}

variable "SCRAP_SIMULATOR_TEST_VISUALIZER_IMAGE" {
  default = "scrap-monitoring-simulator-visualizer:local"
}

variable "SCRAP_SIMULATOR_TEST_EDGE_IMAGE" {
  default = "scrap-monitoring-simulator-camera-edge-bridge:local"
}

group "default" {
  targets = ["simulation-server", "visualizer", "camera-edge-bridge"]
}

group "ci" {
  targets = ["simulation-server", "visualizer", "camera-edge-bridge"]
}

group "ci-amd64" {
  targets = ["simulation-server", "visualizer"]
}

target "simulation-server" {
  context    = "."
  dockerfile = "services/simulation-server/Dockerfile"
  target     = "runtime"
  platforms  = ["linux/amd64"]
  tags       = [SCRAP_SIMULATOR_TEST_SERVER_IMAGE]
  output     = ["type=docker"]
  args = {
    BUILD_REVISION = BUILD_REVISION
    BUILD_VERSION  = BUILD_VERSION
  }
}

target "visualizer" {
  context    = "."
  dockerfile = "services/visualizer/Dockerfile"
  target     = "runtime"
  platforms  = ["linux/amd64"]
  tags       = [SCRAP_SIMULATOR_TEST_VISUALIZER_IMAGE]
  output     = ["type=docker"]
  args = {
    BUILD_REVISION = BUILD_REVISION
    BUILD_VERSION  = BUILD_VERSION
  }
}

target "camera-edge-bridge" {
  context    = "."
  dockerfile = "services/camera-edge-bridge/Dockerfile"
  target     = "runtime"
  platforms  = ["linux/arm64"]
  tags       = [SCRAP_SIMULATOR_TEST_EDGE_IMAGE]
  output     = ["type=docker"]
  args = {
    BUILD_REVISION = BUILD_REVISION
    BUILD_VERSION  = BUILD_VERSION
  }
}

target "docs-lint" {
  context    = "."
  dockerfile = "tools/docs-lint/Dockerfile"
  target     = "check"
  platforms  = ["linux/amd64"]
  output     = ["type=cacheonly"]
}
