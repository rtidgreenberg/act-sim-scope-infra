# Write-drop accounting spike — build.
#
#   export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
#   cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
#   cmake --build build
#
# Produces ./build/write_drop_accounting. rtiddsgen generates the DropProbe type
# support from DropProbe.idl at configure/build time (no committed generated code).

cmake_minimum_required(VERSION 3.11)
project(write_drop_accounting_spike LANGUAGES CXX)

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
    IDL_FILE "${CMAKE_CURRENT_SOURCE_DIR}/DropProbe.idl"
    OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/generated"
    LANG "C++11"
    VAR DropProbe
)

add_executable(write_drop_accounting
    write_drop_accounting.cxx
    ${DropProbe_CXX11_GENERATED_SOURCES}
)
target_include_directories(write_drop_accounting PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}/generated"
)
target_link_libraries(write_drop_accounting
    RTIConnextDDS::cpp2_api Threads::Threads
)
