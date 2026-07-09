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
#   test_discovery_smoke Phase-2 builtin discovery smoke for generated admin endpoints.
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

# --- router_main: Phase-0 skeleton, no DDS entities created ---
add_executable(router_main
    src/router_main.cxx
    src/config/RouterIdentity.cxx
)
target_include_directories(router_main PRIVATE src)

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

# Phase 2: real builtin discovery smoke. Uses UDPv4-only participant QoS, a
# per-process default DDS domain, and real generated admin endpoint topic names.
add_executable(test_discovery_smoke
    test/test_discovery_smoke.cxx
    ${RouterAdminTypes_CXX11_GENERATED_SOURCES}
)
target_include_directories(test_discovery_smoke PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(test_discovery_smoke RTIConnextDDS::cpp2_api)
add_test(NAME discovery_smoke COMMAND test_discovery_smoke)
set_tests_properties(discovery_smoke PROPERTIES
    WORKING_DIRECTORY "${ROUTER_TEST_TMP_DIR}")

# Phase 2.5: thin real runtime spine smoke.
# Proves Connext builtin discovery → DiscoveryDispatcher → RouterController →
# DdsStatusPublisher → observable DDS status sample (D30/D31).
add_executable(test_runtime_spine
    test/test_runtime_spine.cxx
    src/core/RouterState.cxx
    src/core/RouterController.cxx
    src/core/ParticipantRegistry.cxx
    src/core/DiscoveryDispatcher.cxx
    src/core/DdsStatusPublisher.cxx
    ${RouterAdminTypes_CXX11_GENERATED_SOURCES}
)
target_include_directories(test_runtime_spine PRIVATE
    src
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(test_runtime_spine RTIConnextDDS::cpp2_api)
add_test(NAME runtime_spine COMMAND test_runtime_spine)
set_tests_properties(runtime_spine PROPERTIES
    WORKING_DIRECTORY "${ROUTER_TEST_TMP_DIR}")

# Phase 3: one discovered route forwards one topic end-to-end (real EntityFactory,
# TypeResolver, QosResolver, AsyncWaitSetDispatcher). Proves discovery → dynamic entity
# creation → forwarding → status ENABLED, and D32 teardown on source loss.
add_executable(test_route_forward
    test/test_route_forward.cxx
    src/core/RouterState.cxx
    src/core/RouterController.cxx
    src/core/ParticipantRegistry.cxx
    src/core/DiscoveryDispatcher.cxx
    src/core/DdsStatusPublisher.cxx
    src/core/AsyncWaitSetDispatcher.cxx
    ${RouterAdminTypes_CXX11_GENERATED_SOURCES}
)
target_include_directories(test_route_forward PRIVATE
    src
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(test_route_forward RTIConnextDDS::cpp2_api)
add_test(NAME route_forward COMMAND test_route_forward)
set_tests_properties(route_forward PROPERTIES
    WORKING_DIRECTORY "${ROUTER_TEST_TMP_DIR}")

# Phase 4 (steps 1-2): DynamicData forwarding + ContentFilteredTopic. Router loads an
# example type from XML and forwards it as DynamicData with a destination content filter.
add_executable(test_dynamic_forward
    test/test_dynamic_forward.cxx
    src/core/RouterState.cxx
    src/core/RouterController.cxx
    src/core/ParticipantRegistry.cxx
    src/core/DiscoveryDispatcher.cxx
    src/core/DdsStatusPublisher.cxx
    src/core/AsyncWaitSetDispatcher.cxx
    src/core/DynamicRouteFactory.cxx
    ${RouterAdminTypes_CXX11_GENERATED_SOURCES}
)
target_include_directories(test_dynamic_forward PRIVATE
    src
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_compile_definitions(test_dynamic_forward PRIVATE
    ROUTER_CONFIG_DIR="${CMAKE_CURRENT_SOURCE_DIR}/config")
target_link_libraries(test_dynamic_forward RTIConnextDDS::cpp2_api)
add_test(NAME dynamic_forward COMMAND test_dynamic_forward)
set_tests_properties(dynamic_forward PROPERTIES
    WORKING_DIRECTORY "${ROUTER_TEST_TMP_DIR}")

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
