cmake_minimum_required(VERSION 3.20)
project(RuyiServing LANGUAGES C CXX)

if(NOT CMAKE_GENERATOR MATCHES "^Ninja")
    message(FATAL_ERROR
        "Ruyi Serving uses CMake + Ninja as the official build entrypoint.\n"
        "Reconfigure with a Ninja generator, for example:\n"
        "  cmake -S . -B build -G Ninja"
    )
endif()

include(CTest)

find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)

function(ruyi_require_program program_name install_hint)
    find_program(RUYI_${program_name}_EXECUTABLE NAMES ${program_name})
    if(NOT RUYI_${program_name}_EXECUTABLE)
        message(FATAL_ERROR
            "${program_name} was not found in PATH.\n"
            "Install it before configuring Ruyi Serving:\n"
            "  ${install_hint}"
        )
    endif()
endfunction()

function(ruyi_require_python_module module_name install_hint)
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
ruyi_require_program(ninja "sudo apt-get install ninja-build")
ruyi_require_program(pkg-config "sudo apt-get install pkg-config")
ruyi_require_python_module(grpc_tools "${Python3_EXECUTABLE} -m pip install grpcio-tools")
ruyi_require_python_module(pytest "${Python3_EXECUTABLE} -m pip install pytest")
ruyi_require_python_module(requests "${Python3_EXECUTABLE} -m pip install requests")

set(RUYI_PROTO_FILE "${CMAKE_SOURCE_DIR}/proto/compute.proto")
set(RUYI_PYTHON_PROTO_DIR "${CMAKE_SOURCE_DIR}/api_server/proto")
set(RUYI_PYTHON_PROTO_INIT "${RUYI_PYTHON_PROTO_DIR}/__init__.py")
set(RUYI_PYTHON_PROTO_PB2 "${RUYI_PYTHON_PROTO_DIR}/compute_pb2.py")
set(RUYI_PYTHON_PROTO_GRPC "${RUYI_PYTHON_PROTO_DIR}/compute_pb2_grpc.py")

# The API package imports stubs from api_server/proto directly, so CMake keeps the
# generated Python files in the source tree instead of the build directory.
add_custom_command(
    OUTPUT
        "${RUYI_PYTHON_PROTO_INIT}"
        "${RUYI_PYTHON_PROTO_PB2}"
        "${RUYI_PYTHON_PROTO_GRPC}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${RUYI_PYTHON_PROTO_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E touch "${RUYI_PYTHON_PROTO_INIT}"
    COMMAND "${Python3_EXECUTABLE}" -m grpc_tools.protoc
        -I "${CMAKE_SOURCE_DIR}/proto"
        "--python_out=${RUYI_PYTHON_PROTO_DIR}"
        "--grpc_python_out=${RUYI_PYTHON_PROTO_DIR}"
        "${RUYI_PROTO_FILE}"
    DEPENDS "${RUYI_PROTO_FILE}"
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    COMMENT "Generating Python protobuf stubs in api_server/proto"
    VERBATIM
)

add_custom_target(generate_python_proto
    DEPENDS
        "${RUYI_PYTHON_PROTO_INIT}"
        "${RUYI_PYTHON_PROTO_PB2}"
        "${RUYI_PYTHON_PROTO_GRPC}"
)

add_subdirectory(compute_server)

if(BUILD_TESTING)
    # Python API tests are unit/contract tests for the API layer. They stay in CTest
    # for a unified developer and CI entrypoint, but real-model tests remain explicit.
    add_test(
        NAME python.unit.pytest
        COMMAND
            "${CMAKE_COMMAND}" -E env
            "PYTHONPATH=${CMAKE_SOURCE_DIR}"
            "MODELS_CONFIG_FILE=/tmp/ruyi-serving-test-models.json"
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
