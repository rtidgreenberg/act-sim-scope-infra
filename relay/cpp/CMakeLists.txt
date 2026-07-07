# Modern C++ ISC relay — build.
#
#   export NDDSHOME=/home/rti/rti_connext_dds-7.6.0
#   cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
#   cmake --build build
#
# Produces ./build/isc_relay. rtiddsgen generates the ActState type support from
# ActState.idl at configure/build time (no committed generated code).

cmake_minimum_required(VERSION 3.11)
project(isc_relay_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Locate the RTI Connext CMake helpers (FindRTIConnextDDS + codegen).
if(NOT DEFINED CONNEXTDDS_DIR AND DEFINED ENV{NDDSHOME})
    set(CONNEXTDDS_DIR "$ENV{NDDSHOME}")
endif()
if(NOT CONNEXTDDS_DIR)
    message(FATAL_ERROR "Set NDDSHOME (or -DCONNEXTDDS_DIR=...) to your Connext install.")
endif()
list(APPEND CMAKE_MODULE_PATH "${CONNEXTDDS_DIR}/resource/cmake")

# COMPONENTS: core pulls in the Modern C++ (cpp2) libraries; Network Capture ships
# in the core libraries.
find_package(RTIConnextDDS "7.6.0" REQUIRED COMPONENTS core)

include(ConnextDdsCodegen)

# Generate ActState type support (C++11 == "Modern C++"). Sets ActState_CXX11_*.
connextdds_rtiddsgen_run(
    IDL_FILE "${CMAKE_CURRENT_SOURCE_DIR}/ActState.idl"
    OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/generated"
    LANG "C++11"
    VAR ActState
)

add_executable(isc_relay
    isc_relay.cxx
    ${ActState_CXX11_GENERATED_SOURCES}
)

target_include_directories(isc_relay PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}/generated"
)

# RTIConnextDDS::cpp2_api == Modern C++ API + its transitive core/c dependencies.
target_link_libraries(isc_relay
    RTIConnextDDS::cpp2_api
)
