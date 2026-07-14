# ACT C++ router — Phase 0 build skeleton.
#
#   export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
#   cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
#   cmake --build build
#   ctest --test-dir build --output-on-failure
#
# Targets:
#   router_main          Phase-0 entry point (no DDS): validate config, print identity.
#   test_config_identity no-DDS unit test for the config identity reader.
#   test_admin_types     compiles + exercises the generated admin types (links Connext).
# DDS integration tests (discovery smoke, forwarding, auto-QoS) live in Python under
# router/test_e2e/ — see that dir's README and D52.
#
# Modeled on the proven relay/cpp/CMakeLists.txt. rtiddsgen generates RouterAdminTypes
# from admin/RouterAdminTypes.idl at configure/build time (no committed generated code).

cmake_minimum_required(VERSION 3.11)
project(act_router_cpp LANGUAGES CXX)

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

find_package(RTIConnextDDS "7.7.0" REQUIRED COMPONENTS core)

include(ConnextDdsCodegen)

# yaml-cpp for route/participant config parsing (D36). Fetched + built locally into the
# build tree (never the vboxsf share). Requires network at first configure.
include(FetchContent)
FetchContent_Declare(yaml-cpp
    GIT_REPOSITORY https://github.com/jbeder/yaml-cpp.git
    GIT_TAG 0.8.0)
set(YAML_CPP_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(YAML_CPP_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(YAML_CPP_FORMAT_SOURCE OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(yaml-cpp)

# Generate the admin command/status type support (C++11 == "Modern C++").
# Sets RouterAdminTypes_CXX11_GENERATED_SOURCES and the generated/ include dir.
connextdds_rtiddsgen_run(
    IDL_FILE "${CMAKE_CURRENT_SOURCE_DIR}/admin/RouterAdminTypes.idl"
    OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/generated"
    LANG "C++11"
    VAR RouterAdminTypes
)

# --- router_main: real config-driven router process (Phases 3-5 wiring) ---
add_executable(router_main
    src/router_main.cxx
    src/config/RouteConfigParser.cxx
    src/core/RouterState.cxx
    src/core/RouterController.cxx
    src/core/ParticipantRegistry.cxx
    src/core/DiscoveryDispatcher.cxx
    src/core/DdsStatusPublisher.cxx
    src/core/CommandReader.cxx
    src/core/AsyncWaitSetDispatcher.cxx
    src/core/DynamicRouteFactory.cxx
    ${RouterAdminTypes_CXX11_GENERATED_SOURCES}
)
target_include_directories(router_main PRIVATE
    src
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(router_main RTIConnextDDS::cpp2_api yaml-cpp::yaml-cpp)

# --- Tests ---
enable_testing()

set(ROUTER_TEST_TMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../tmp/router_tests")
file(MAKE_DIRECTORY "${ROUTER_TEST_TMP_DIR}")

# no-DDS config identity unit test
add_executable(test_config_identity
    test/test_config_identity.cxx
    src/config/RouterIdentity.cxx
)
target_include_directories(test_config_identity PRIVATE src)
target_compile_definitions(test_config_identity PRIVATE
    ROUTER_SAMPLE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/config")
add_test(NAME config_identity COMMAND test_config_identity)

# Phase 1: controller-owned state machine conformance (fakes for all DDS seams — D3;
# links Connext for the generated admin types only, creates no DDS entities)
add_executable(test_controller_phase1
    test/test_controller_phase1.cxx
    src/core/RouterState.cxx
    src/core/RouterController.cxx
    ${RouterAdminTypes_CXX11_GENERATED_SOURCES}
)
target_include_directories(test_controller_phase1 PRIVATE
    src
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(test_controller_phase1 RTIConnextDDS::cpp2_api)
add_test(NAME controller_phase1 COMMAND test_controller_phase1)

# generated admin types compile/usability test (links the Modern C++ API)
add_executable(test_admin_types
    test/test_admin_types.cxx
    ${RouterAdminTypes_CXX11_GENERATED_SOURCES}
)
target_include_directories(test_admin_types PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(test_admin_types RTIConnextDDS::cpp2_api)
add_test(NAME admin_types COMMAND test_admin_types)

# Phase 2 builtin discovery smoke migrated to Python (D52 test-suite policy: integration
# tests are Python, unit tests stay C++). See router/test_e2e/test_discovery_smoke.py.

# Phase 2.5 runtime-spine smoke (builtin discovery -> DiscoveryDispatcher ->
# RouterController -> DdsStatusPublisher -> observable status) migrated to Python against
# the real router_main + real factory (D52 test-suite policy). Its distinctive coverage —
# the D15 same-node-ignore contract (a same-node router publication is ignored; a genuine
# app publication is routed) — is router/test_e2e/test_same_node_ignore.py. The former C++
# test_runtime_spine.cxx (which asserted this via a fake factory's create-count) was
# retired accordingly.

# Phase 3 route forwarding and Phase 4 DynamicData + ContentFilteredTopic forwarding are
# now covered end-to-end in Python against the real router_main pair (D52 test-suite
# policy): router/test_e2e/test_platform_status_route.py (forwarding) and
# test_control_command_route.py (DynamicData + msg.destination CFT). The former C++
# test_route_forward.cxx / test_dynamic_forward.cxx were retired accordingly.

# Phase 5 asymmetric auto QoS + output readiness (D39/D42/D45) is now covered end-to-end
# in Python against the real router_main (D52 test-suite policy): readiness gating,
# resolved reader/writer QoS summaries, in-place deadline tightening, and incompatible-QoS
# warnings are asserted off the RouterStatus stream in router/test_e2e/test_auto_qos.py.
# The former C++ test_auto_qos.cxx was retired accordingly.

# Phase 4 (step 3): role-aware route config parsing (yaml-cpp, no DDS).
add_executable(test_route_config
    test/test_route_config.cxx
    src/config/RouteConfigParser.cxx
    ${RouterAdminTypes_CXX11_GENERATED_SOURCES}
)
target_include_directories(test_route_config PRIVATE
    src
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_compile_definitions(test_route_config PRIVATE
    ROUTER_CONFIG_DIR="${CMAKE_CURRENT_SOURCE_DIR}/config")
target_link_libraries(test_route_config RTIConnextDDS::cpp2_api yaml-cpp::yaml-cpp)
add_test(NAME route_config COMMAND test_route_config)
