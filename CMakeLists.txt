cmake_minimum_required(VERSION 3.20)
project(LarkMemoryCore LANGUAGES C CXX)

if(NOT CMAKE_GENERATOR MATCHES "^Ninja")
    message(FATAL_ERROR
        "LarkMemoryCore uses CMake + Ninja as the official build entrypoint.\n"
        "Reconfigure with a Ninja generator, for example:\n"
        "  cmake -S . -B build -G Ninja"
    )
endif()

include(CTest)

find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)

function(lark_memory_core_require_program program_name install_hint)
    find_program(LARK_MEMORY_CORE_${program_name}_EXECUTABLE
        NAMES ${program_name}
        HINTS "$ENV{HOME}/.cargo/bin"
    )
    if(NOT LARK_MEMORY_CORE_${program_name}_EXECUTABLE)
        message(FATAL_ERROR
            "${program_name} was not found in PATH.\n"
            "Install it before configuring LarkMemoryCore:\n"
            "  ${install_hint}"
        )
    endif()
    set(LARK_MEMORY_CORE_${program_name}_EXECUTABLE "${LARK_MEMORY_CORE_${program_name}_EXECUTABLE}" PARENT_SCOPE)
endfunction()

function(lark_memory_core_require_python_module module_name install_hint)
    execute_process(
        COMMAND "${Python3_EXECUTABLE}" -c "import ${module_name}"
        RESULT_VARIABLE import_status
        ERROR_VARIABLE import_error
        OUTPUT_QUIET
    )
    if(NOT import_status EQUAL 0)
        string(STRIP "${import_error}" import_error)
        message(FATAL_ERROR
            "Python module '${module_name}' is required but could not be imported.\n"
            "Install it with the active Python interpreter:\n"
            "  ${install_hint}\n"
            "Import error: ${import_error}"
        )
    endif()
endfunction()

# Root configure is the single dependency gate for developers, CI, and deploy scripts.
lark_memory_core_require_program(ninja "sudo apt-get install ninja-build")
lark_memory_core_require_program(pkg-config "sudo apt-get install pkg-config")
lark_memory_core_require_program(cargo "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")
lark_memory_core_require_python_module(grpc_tools "${Python3_EXECUTABLE} -m pip install grpcio-tools")
lark_memory_core_require_python_module(pytest "${Python3_EXECUTABLE} -m pip install pytest")
lark_memory_core_require_python_module(requests "${Python3_EXECUTABLE} -m pip install requests")

set(LARK_MEMORY_CORE_PROTO_FILE "${CMAKE_SOURCE_DIR}/proto/compute.proto")
set(LARK_MEMORY_CORE_PYTHON_PROTO_DIR "${CMAKE_SOURCE_DIR}/api_server/proto")
set(LARK_MEMORY_CORE_PYTHON_PROTO_INIT "${LARK_MEMORY_CORE_PYTHON_PROTO_DIR}/__init__.py")
set(LARK_MEMORY_CORE_PYTHON_PROTO_PB2 "${LARK_MEMORY_CORE_PYTHON_PROTO_DIR}/compute_pb2.py")
set(LARK_MEMORY_CORE_PYTHON_PROTO_GRPC "${LARK_MEMORY_CORE_PYTHON_PROTO_DIR}/compute_pb2_grpc.py")

# The API package imports stubs from api_server/proto directly, so CMake keeps the
# generated Python files in the source tree instead of the build directory.
add_custom_command(
    OUTPUT
        "${LARK_MEMORY_CORE_PYTHON_PROTO_INIT}"
        "${LARK_MEMORY_CORE_PYTHON_PROTO_PB2}"
        "${LARK_MEMORY_CORE_PYTHON_PROTO_GRPC}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${LARK_MEMORY_CORE_PYTHON_PROTO_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E touch "${LARK_MEMORY_CORE_PYTHON_PROTO_INIT}"
    COMMAND "${Python3_EXECUTABLE}" -m grpc_tools.protoc
        -I "${CMAKE_SOURCE_DIR}/proto"
        "--python_out=${LARK_MEMORY_CORE_PYTHON_PROTO_DIR}"
        "--grpc_python_out=${LARK_MEMORY_CORE_PYTHON_PROTO_DIR}"
        "${LARK_MEMORY_CORE_PROTO_FILE}"
    DEPENDS "${LARK_MEMORY_CORE_PROTO_FILE}"
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    COMMENT "Generating Python protobuf stubs in api_server/proto"
    VERBATIM
)

add_custom_target(generate_python_proto
    DEPENDS
        "${LARK_MEMORY_CORE_PYTHON_PROTO_INIT}"
        "${LARK_MEMORY_CORE_PYTHON_PROTO_PB2}"
        "${LARK_MEMORY_CORE_PYTHON_PROTO_GRPC}"
)

add_subdirectory(compute_server)

if(BUILD_TESTING)
    add_test(
        NAME rust.unit.cargo
        COMMAND "${LARK_MEMORY_CORE_cargo_EXECUTABLE}" test --workspace
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    )
    set_tests_properties(
        rust.unit.cargo
        PROPERTIES
            LABELS "rust-unit"
    )

    # Python API tests are unit/contract tests for the API layer. They stay in CTest
    # for a unified developer and CI entrypoint, but real-model tests remain explicit.
    add_test(
        NAME python.unit.pytest
        COMMAND
            "${CMAKE_COMMAND}" -E env
            "PYTHONPATH=${CMAKE_SOURCE_DIR}"
            "MODELS_CONFIG_FILE=/tmp/lark-memory-core-test-models.json"
            "CLUSTER_CONFIG_FILE="
            "${Python3_EXECUTABLE}" -m pytest -q "${CMAKE_SOURCE_DIR}/tests/python"
    )
    set_tests_properties(
        python.unit.pytest
        PROPERTIES
            WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
            LABELS "python-unit"
    )
endif()
