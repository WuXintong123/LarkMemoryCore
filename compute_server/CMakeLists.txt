cmake_minimum_required(VERSION 3.20)
project(ComputeServer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(CTest)

include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/LarkMemoryCoreDependencies.cmake)
include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/LarkMemoryCoreProto.cmake)

add_executable(compute_server
    src/Main.cpp
    src/ComputeSemaphore.cpp
    src/ComputeServiceImpl.cpp
    src/ComputeFunctions.cpp
    src/ModelConfig.cpp
    src/TokenCounter.cpp
    src/StructuredLogger.cpp
    ${PROTO_SRCS}
    ${GRPC_SRCS}
)

# Root CMake exposes build/bin/compute_server as the single canonical runtime path
# so deploy scripts, Docker, and systemd units all point at the same artifact.
set_target_properties(compute_server PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

target_include_directories(compute_server PRIVATE
    ${CMAKE_BINARY_DIR}
)

if(NOT TARGET PkgConfig::PROTOBUF_PC)
    target_include_directories(compute_server PRIVATE
        ${Protobuf_INCLUDE_DIRS}
    )
endif()

lark_memory_core_target_link_grpc_and_protobuf(compute_server)

install(TARGETS compute_server DESTINATION bin)

include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/LarkMemoryCoreTests.cmake)
lark_memory_core_add_compute_tests()
