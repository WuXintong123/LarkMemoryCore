if(CMAKE_SOURCE_DIR STREQUAL PROJECT_SOURCE_DIR)
    set(PROTO_PATH "${CMAKE_SOURCE_DIR}/../proto")
else()
    set(PROTO_PATH "${CMAKE_SOURCE_DIR}/proto")
endif()
set(PROTO_FILE "${PROTO_PATH}/compute.proto")

set(PROTO_SRCS "${CMAKE_BINARY_DIR}/compute.pb.cc")
set(PROTO_HDRS "${CMAKE_BINARY_DIR}/compute.pb.h")
set(GRPC_SRCS "${CMAKE_BINARY_DIR}/compute.grpc.pb.cc")
set(GRPC_HDRS "${CMAKE_BINARY_DIR}/compute.grpc.pb.h")

if(TARGET gRPC::grpc_cpp_plugin)
    get_target_property(GRPC_CPP_PLUGIN_EXECUTABLE gRPC::grpc_cpp_plugin LOCATION)
elseif(DEFINED GRPC_CPP_PLUGIN AND EXISTS "${GRPC_CPP_PLUGIN}")
    set(GRPC_CPP_PLUGIN_EXECUTABLE "${GRPC_CPP_PLUGIN}")
else()
    find_program(GRPC_CPP_PLUGIN_EXECUTABLE
        NAMES grpc_cpp_plugin
        PATHS ${RUYI_MANUAL_BIN_PATHS}
    )
endif()

if(NOT GRPC_CPP_PLUGIN_EXECUTABLE)
    message(FATAL_ERROR
        "grpc_cpp_plugin not found. Install the gRPC compiler plugin.\n"
        "  ${RUYI_GRPC_INSTALL_HINT}"
    )
endif()

add_custom_command(
    OUTPUT "${PROTO_SRCS}" "${PROTO_HDRS}" "${GRPC_SRCS}" "${GRPC_HDRS}"
    COMMAND "${PROTOC_EXECUTABLE}"
    ARGS --grpc_out "${CMAKE_BINARY_DIR}"
         --cpp_out "${CMAKE_BINARY_DIR}"
         -I "${PROTO_PATH}"
         --plugin=protoc-gen-grpc="${GRPC_CPP_PLUGIN_EXECUTABLE}"
         --experimental_allow_proto3_optional
         "${PROTO_FILE}"
    DEPENDS "${PROTO_FILE}"
    COMMENT "Generating C++ code from ${PROTO_FILE}"
)
