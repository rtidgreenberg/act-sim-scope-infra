# Instance-state recovery spike — build.
#
#   export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
#   cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
#   cmake --build build
#
# Produces ./build/state_writer and ./build/state_reader. rtiddsgen generates the
# IscState type support from IscState.idl at configure/build time.

cmake_minimum_required(VERSION 3.11)
project(isc_recovery_spike LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

if(NOT DEFINED CONNEXTDDS_DIR AND DEFINED ENV{NDDSHOME})
    set(CONNEXTDDS_DIR "$ENV{NDDSHOME}")
endif()
if(NOT CONNEXTDDS_DIR)
    message(FATAL_ERROR "Set NDDSHOME (or -DCONNEXTDDS_DIR=...) to your Connext install.")
endif()
list(APPEND CMAKE_MODULE_PATH "${CONNEXTDDS_DIR}/resource/cmake")

find_package(RTIConnextDDS "7.7.0" REQUIRED COMPONENTS core)

include(ConnextDdsCodegen)

connextdds_rtiddsgen_run(
    IDL_FILE "${CMAKE_CURRENT_SOURCE_DIR}/IscState.idl"
    OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/generated"
    LANG "C++11"
    VAR IscState
)

add_executable(state_writer state_writer.cxx ${IscState_CXX11_GENERATED_SOURCES})
target_include_directories(state_writer PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(state_writer RTIConnextDDS::cpp2_api Threads::Threads)

add_executable(state_reader state_reader.cxx ${IscState_CXX11_GENERATED_SOURCES})
target_include_directories(state_reader PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(state_reader RTIConnextDDS::cpp2_api Threads::Threads)

add_executable(state_relay state_relay.cxx ${IscState_CXX11_GENERATED_SOURCES})
target_include_directories(state_relay PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(state_relay RTIConnextDDS::cpp2_api Threads::Threads)
