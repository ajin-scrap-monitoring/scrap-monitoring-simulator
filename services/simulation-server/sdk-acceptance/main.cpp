#include "sl_lidar.h"
#include "sl_lidar_driver.h"

#include <arpa/inet.h>

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

constexpr std::uint32_t kControlTimeoutMs = 5'000;

struct SensorResult {
    std::string host;
    int port;
    std::size_t node_count;
    std::size_t measured_count;
    sl::LidarScanMode mode;
    std::array<std::uint8_t, 16> serial;
};

struct SensorEndpoint {
    std::string host;
    int port;
};

void require_result(sl_result result, const char* operation) {
    if (SL_IS_FAIL(result)) {
        throw std::runtime_error(
            std::string(operation) + " failed: " + std::to_string(result));
    }
}

SensorResult accept_sensor(const SensorEndpoint& endpoint) {
    auto channel_result =
        sl::createUdpChannel(endpoint.host.c_str(), endpoint.port);
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
    require_result(driver->getDeviceInfo(info, kControlTimeoutMs), "getDeviceInfo");
    require_result(driver->getHealth(health, kControlTimeoutMs), "getHealth");
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

    require_result(driver->stop(kControlTimeoutMs), "stop");
    driver->disconnect();
    std::array<std::uint8_t, 16> serial{};
    std::copy(std::begin(info.serialnum), std::end(info.serialnum), serial.begin());
    return SensorResult{
        endpoint.host, endpoint.port, count, measured_count, mode, serial};
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

SensorEndpoint parse_endpoint(const char* value) {
    const std::string text(value);
    const auto separator = text.rfind(':');
    if (separator == std::string::npos || separator == 0 ||
        separator == text.size() - 1) {
        throw std::runtime_error("endpoint must use HOST:PORT format");
    }
    return SensorEndpoint{
        text.substr(0, separator), parse_port(text.substr(separator + 1).c_str())};
}

void validate_endpoints(const std::array<SensorEndpoint, 2>& endpoints) {
    std::array<in_addr, 2> addresses{};
    for (std::size_t index = 0; index < endpoints.size(); ++index) {
        const auto& endpoint = endpoints[index];
        if (endpoint.port != 8089) {
            throw std::runtime_error("both UDP endpoints must use port 8089");
        }
        if (inet_pton(AF_INET, endpoint.host.c_str(), &addresses[index]) != 1 ||
            addresses[index].s_addr == htonl(INADDR_ANY)) {
            throw std::runtime_error(
                "endpoint host must be a non-wildcard IPv4 address");
        }
    }
    if (addresses[0].s_addr == addresses[1].s_addr) {
        throw std::runtime_error("expected two distinct UDP endpoint IPs");
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        std::array<SensorEndpoint, 2> endpoints = {{
            {"127.0.0.2", 8089},
            {"127.0.0.3", 8089},
        }};
        if (argc == 3) {
            endpoints = {{parse_endpoint(argv[1]), parse_endpoint(argv[2])}};
        } else if (argc != 1) {
            throw std::runtime_error("expected zero or two HOST:PORT endpoints");
        }
        validate_endpoints(endpoints);

        std::array<std::future<SensorResult>, 2> futures = {
            std::async(std::launch::async, accept_sensor, endpoints[0]),
            std::async(std::launch::async, accept_sensor, endpoints[1]),
        };
        const std::array<SensorResult, 2> results = {
            futures[0].get(),
            futures[1].get(),
        };
        if (results[0].serial == results[1].serial) {
            throw std::runtime_error(
                "UDP endpoints returned the same sensor serial");
        }
        for (const auto& result : results) {
            std::cout << "endpoint=" << result.host << ':' << result.port
                      << " mode=" << result.mode.id
                      << " nodes=" << result.node_count
                      << " measured=" << result.measured_count << '\n';
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "SDK acceptance failed: " << error.what() << '\n';
        return 1;
    }
}
