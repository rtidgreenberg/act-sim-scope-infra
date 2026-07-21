# Modern C++ partition-discovery spike -- build.
#
#   export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
#   cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
#   cmake --build build
#
# Produces ./build/spdp_partition_discovery_spike. No IDL/codegen -- this spike only
# touches DomainParticipant-level discovery, no topics or data types.

cmake_minimum_required(VERSION 3.11)
project(spdp_partition_discovery_spike LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED CONNEXTDDS_DIR AND DEFINED ENV{NDDSHOME})
    set(CONNEXTDDS_DIR "$ENV{NDDSHOME}")
endif()
if(NOT CONNEXTDDS_DIR)
    message(FATAL_ERROR "Set NDDSHOME (or -DCONNEXTDDS_DIR=...) to your Connext install.")
endif()
list(APPEND CMAKE_MODULE_PATH "${CONNEXTDDS_DIR}/resource/cmake")

find_package(RTIConnextDDS "7.7.0" REQUIRED COMPONENTS core)

add_executable(spdp_partition_discovery_spike
    spdp_partition_discovery_spike.cxx
)

target_link_libraries(spdp_partition_discovery_spike
    RTIConnextDDS::cpp2_api
)
