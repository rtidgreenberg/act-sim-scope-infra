find_package(RTIConnextDDS "7.7.0" REQUIRED COMPONENTS core routing_service)
set_target_properties(yaml-cpp PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_library(act_rs_model STATIC
    GatewayModel.cxx
    ../src/config/RouteConfigParser.cxx
    ../src/config/Sha256.cxx
    ../src/core/WanCodec.cxx
    ../src/core/WanInterests.cxx)
set_target_properties(act_rs_model PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(act_rs_model PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/../src"
    "${CMAKE_CURRENT_BINARY_DIR}/../generated")
target_link_libraries(act_rs_model PUBLIC act_rs_types yaml-cpp::yaml-cpp)

add_library(act_gateway_processor SHARED
    GatewayProcessor.cxx
    GatewayMonitor.cxx
    ../src/core/PresenceMonitor.cxx
    ../src/core/LinkStatsCollector.cxx)
target_link_libraries(act_gateway_processor PRIVATE
    act_rs_model RTIConnextDDS::routing_service_cpp2)
if(UNIX AND NOT APPLE)
    set_property(TARGET act_gateway_processor APPEND_STRING PROPERTY LINK_FLAGS
        " -Wl,--no-undefined")
endif()

add_executable(act_rs_config ConfigMain.cxx)
target_link_libraries(act_rs_config PRIVATE act_rs_model)
