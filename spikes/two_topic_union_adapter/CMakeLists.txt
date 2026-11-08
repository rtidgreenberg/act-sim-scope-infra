cmake_minimum_required(VERSION 3.11)
project(two_topic_union_adapter LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT DEFINED CONNEXTDDS_DIR AND DEFINED ENV{NDDSHOME})
    set(CONNEXTDDS_DIR "$ENV{NDDSHOME}")
endif()
if(NOT CONNEXTDDS_DIR)
    message(FATAL_ERROR "Set NDDSHOME or CONNEXTDDS_DIR to the Connext SDK.")
endif()
list(APPEND CMAKE_MODULE_PATH "${CONNEXTDDS_DIR}/resource/cmake")
find_package(RTIConnextDDS "7.7.0" REQUIRED COMPONENTS core)

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(MODEL "${REPO_ROOT}/harness_v2/datamodel/ActTypes.idl")
set(FIXTURE "${REPO_ROOT}/router/qualification/fixtures/two_topic_qualification.idl")
set(TYPE_DIR "${CMAKE_CURRENT_BINARY_DIR}/types")
add_custom_command(
    OUTPUT "${TYPE_DIR}/ActTypes.xml"
           "${TYPE_DIR}/two_topic_qualification.xml"
           "${TYPE_DIR}/adapter.xml"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${TYPE_DIR}"
    COMMAND "${CONNEXTDDS_DIR}/bin/rtiddsgen" -convertToXml
            -I "${REPO_ROOT}/harness_v2/datamodel" -d "${TYPE_DIR}" "${MODEL}"
    COMMAND "${CONNEXTDDS_DIR}/bin/rtiddsgen" -convertToXml
            -I "${REPO_ROOT}/harness_v2/datamodel" -d "${TYPE_DIR}" "${FIXTURE}"
    COMMAND "${CONNEXTDDS_DIR}/bin/rtiddsgen" -convertToXml
            -I "${REPO_ROOT}/harness_v2/datamodel" -d "${TYPE_DIR}"
            -I "${REPO_ROOT}/router/qualification/fixtures"
            "${CMAKE_CURRENT_SOURCE_DIR}/adapter.idl"
    DEPENDS "${MODEL}" "${FIXTURE}" "${CMAKE_CURRENT_SOURCE_DIR}/adapter.idl"
    VERBATIM
)
add_custom_target(adapter_types DEPENDS "${TYPE_DIR}/adapter.xml")
add_executable(union_adapter union_adapter.cxx)
add_dependencies(union_adapter adapter_types)
target_compile_options(union_adapter PRIVATE -Wall -Wextra -Werror)
target_link_libraries(union_adapter RTIConnextDDS::cpp2_api)
