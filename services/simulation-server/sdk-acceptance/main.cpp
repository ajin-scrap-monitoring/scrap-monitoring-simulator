#include "sl_lidar.h"
#include "sl_lidar_driver.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <future>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

struct SensorResult {
    int port;
    std::size_t node_count;
    std::size_t measured_count;
    sl::LidarScanMode mode;
};

void require_result(sl_result result, const char* operation) {
    if (SL_IS_FAIL(result)) {
        throw std::runtime_error(
            std::string(operation) + " failed: " + std::to_string(result));
    }
}

SensorResult accept_sensor(int port) {
    auto channel_result = sl::createUdpChannel("127.0.0.1", port);
    if (!channel_result) {
        throw std::runtime_error("createUdpChannel failed");
    }
    std::unique_ptr<sl::IChannel> channel(*channel_result);

    auto driver_result = sl::createLidarDriver();
    if (!driver_result) {
        throw std::runtime_error("createLidarDriver failed");
    }
    std::unique_ptr<sl::ILidarDriver> driver(*driver_result);
    require_result(driver->connect(channel.get()), "connect");

    sl_lidar_response_device_info_t info{};
    sl_lidar_response_device_health_t health{};
    require_result(driver->getDeviceInfo(info, 2'000), "getDeviceInfo");
    require_result(driver->getHealth(health, 2'000), "getHealth");
    if (info.firmware_version < 0x0118) {
        throw std::runtime_error("firmware does not advertise configuration support");
    }
    if (health.status == SL_LIDAR_STATUS_ERROR) {
        throw std::runtime_error("device health is error");
    }

    sl::LidarScanMode mode{};
    require_result(driver->startScan(false, true, 0, &mode), "startScan");
    if (mode.id != SL_LIDAR_CONF_SCAN_COMMAND_HQ ||
        mode.ans_type != SL_LIDAR_ANS_TYPE_MEASUREMENT_HQ ||
        mode.us_per_sample <= 0.0F || mode.max_distance <= 0.0F) {
        throw std::runtime_error("unexpected typical scan mode");
    }

    std::vector<sl_lidar_response_measurement_node_hq_t> nodes(32'768);
    std::size_t count = nodes.size();
    require_result(driver->grabScanDataHq(nodes.data(), count, 5'000),
                   "grabScanDataHq");
    if (count == 0 || count > nodes.size()) {
        throw std::runtime_error("SDK returned an invalid HQ node count");
    }
    require_result(driver->ascendScanData(nodes.data(), count), "ascendScanData");
    const auto measured_count = static_cast<std::size_t>(std::count_if(
        nodes.begin(), nodes.begin() + static_cast<std::ptrdiff_t>(count),
        [](const auto& node) { return node.dist_mm_q2 > 0; }));
    if (measured_count == 0) {
        throw std::runtime_error("SDK returned no measured HQ nodes");
    }

    require_result(driver->stop(2'000), "stop");
    driver->disconnect();
    return SensorResult{port, count, measured_count, mode};
}

int parse_port(const char* value) {
    const std::string text(value);
    std::size_t consumed = 0;
    const int port = std::stoi(text, &consumed);
    if (consumed != text.size() || port < 1 || port > 65'535) {
        throw std::runtime_error("port must be from 1 through 65535");
    }
    return port;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const std::array<int, 2> ports = {
            argc > 1 ? parse_port(argv[1]) : 8089,
            argc > 2 ? parse_port(argv[2]) : 8090,
        };
        if (argc > 3 || ports[0] == ports[1]) {
            throw std::runtime_error("expected two distinct UDP ports");
        }

        std::array<std::future<SensorResult>, 2> futures = {
            std::async(std::launch::async, accept_sensor, ports[0]),
            std::async(std::launch::async, accept_sensor, ports[1]),
        };
        for (auto& future : futures) {
            const auto result = future.get();
            std::cout << "port=" << result.port << " mode=" << result.mode.id
                      << " nodes=" << result.node_count
                      << " measured=" << result.measured_count << '\n';
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "SDK acceptance failed: " << error.what() << '\n';
        return 1;
    }
}
