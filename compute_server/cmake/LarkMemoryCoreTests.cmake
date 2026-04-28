function(lark_memory_core_add_compute_tests)
    if(NOT BUILD_TESTING)
        return()
    endif()

    find_package(GTest QUIET)
    if(NOT GTest_FOUND)
        message(FATAL_ERROR
            "GTest not found. Install the C++ test dependency.\n"
            "  ${LARK_MEMORY_CORE_GTEST_INSTALL_HINT}"
        )
    endif()

    set(COMPUTE_TEST_INCLUDE_DIRS
        ${PROJECT_SOURCE_DIR}/src
    )

    function(lark_memory_core_register_compute_test target_name test_name)
        target_include_directories(${target_name} PRIVATE ${COMPUTE_TEST_INCLUDE_DIRS})
        target_link_libraries(${target_name} PRIVATE GTest::gtest_main)
        add_test(NAME ${test_name} COMMAND ${target_name})
        set_tests_properties(${test_name} PROPERTIES LABELS "cpp")
        list(APPEND LARK_MEMORY_CORE_COMPUTE_TEST_TARGETS ${target_name})
        set(LARK_MEMORY_CORE_COMPUTE_TEST_TARGETS "${LARK_MEMORY_CORE_COMPUTE_TEST_TARGETS}" PARENT_SCOPE)
    endfunction()

    add_executable(test_token_counter
        ${PROJECT_SOURCE_DIR}/../tests/cpp/test_token_counter.cpp
        src/TokenCounter.cpp
    )
    lark_memory_core_register_compute_test(test_token_counter cpp.test_token_counter)

    add_executable(test_model_validation
        ${PROJECT_SOURCE_DIR}/../tests/cpp/test_model_validation.cpp
        src/ModelConfig.cpp
        src/StructuredLogger.cpp
    )
    lark_memory_core_register_compute_test(test_model_validation cpp.test_model_validation)

    add_executable(test_structured_logger
        ${PROJECT_SOURCE_DIR}/../tests/cpp/test_structured_logger.cpp
        src/StructuredLogger.cpp
    )
    lark_memory_core_register_compute_test(test_structured_logger cpp.test_structured_logger)

    add_executable(test_compute_semaphore
        ${PROJECT_SOURCE_DIR}/../tests/cpp/test_compute_semaphore.cpp
        src/ComputeSemaphore.cpp
    )
    lark_memory_core_register_compute_test(test_compute_semaphore cpp.test_compute_semaphore)

    add_executable(test_compute_functions
        ${PROJECT_SOURCE_DIR}/../tests/cpp/test_compute_functions.cpp
        src/ComputeFunctions.cpp
        src/ModelConfig.cpp
        src/StructuredLogger.cpp
        src/TokenCounter.cpp
    )
    lark_memory_core_register_compute_test(test_compute_functions cpp.test_compute_functions)

    add_executable(test_compute_service_impl
        ${PROJECT_SOURCE_DIR}/../tests/cpp/test_compute_service_impl.cpp
        src/ComputeSemaphore.cpp
        src/ComputeServiceImpl.cpp
        src/ComputeFunctions.cpp
        src/ModelConfig.cpp
        src/StructuredLogger.cpp
        src/TokenCounter.cpp
        ${PROTO_SRCS}
        ${GRPC_SRCS}
    )
    target_include_directories(test_compute_service_impl PRIVATE ${CMAKE_BINARY_DIR})
    lark_memory_core_target_link_grpc_and_protobuf(test_compute_service_impl)
    lark_memory_core_register_compute_test(test_compute_service_impl cpp.test_compute_service_impl)

    add_custom_target(compute_server_tests DEPENDS ${LARK_MEMORY_CORE_COMPUTE_TEST_TARGETS})
endfunction()
