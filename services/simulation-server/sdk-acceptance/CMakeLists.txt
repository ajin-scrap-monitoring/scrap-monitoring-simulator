cmake_minimum_required(VERSION 3.25)
project(scrap_monitoring_sdk_acceptance LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)
FetchContent_Declare(
  rplidar
  URL https://github.com/Slamtec/rplidar_sdk/archive/99478e5fb90de3b4a6db0080acacd373f8b36869.tar.gz
  URL_HASH SHA256=0484d68df785b69123f749328653b11228d89546930ad6ae0573053708676713
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(rplidar)

set(SDK ${rplidar_SOURCE_DIR}/sdk)
file(GLOB SDK_UNPACKER CONFIGURE_DEPENDS
  ${SDK}/src/dataunpacker/*.cpp
  ${SDK}/src/dataunpacker/unpacker/*.cpp
)

add_library(slamtec_sdk STATIC
  ${SDK}/src/sl_lidar_driver.cpp
  ${SDK}/src/hal/thread.cpp
  ${SDK}/src/sl_crc.cpp
  ${SDK}/src/sl_serial_channel.cpp
  ${SDK}/src/sl_lidarprotocol_codec.cpp
  ${SDK}/src/sl_async_transceiver.cpp
  ${SDK}/src/sl_tcp_channel.cpp
  ${SDK}/src/sl_udp_channel.cpp
  ${SDK}/src/arch/linux/net_serial.cpp
  ${SDK}/src/arch/linux/net_socket.cpp
  ${SDK}/src/arch/linux/timer.cpp
  ${SDK_UNPACKER}
)
target_include_directories(slamtec_sdk
  PUBLIC ${SDK}/include
  PRIVATE ${SDK}/src ${SDK}/src/dataunpacker ${SDK}/src/dataunpacker/unpacker
)
target_link_libraries(slamtec_sdk PUBLIC pthread)

add_executable(sdk-acceptance main.cpp)
target_compile_options(sdk-acceptance PRIVATE -Wall -Wextra -Werror)
target_link_libraries(sdk-acceptance PRIVATE slamtec_sdk)
