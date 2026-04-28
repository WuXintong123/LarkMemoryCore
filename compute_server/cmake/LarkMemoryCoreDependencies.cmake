set(LARK_MEMORY_CORE_PKGCONFIG_INSTALL_HINT "")
set(LARK_MEMORY_CORE_PROTOBUF_INSTALL_HINT "")
set(LARK_MEMORY_CORE_GRPC_INSTALL_HINT "")
set(LARK_MEMORY_CORE_OPENSSL_INSTALL_HINT "")
set(LARK_MEMORY_CORE_GTEST_INSTALL_HINT "")

if(EXISTS "/etc/debian_version")
    set(LARK_MEMORY_CORE_PKGCONFIG_INSTALL_HINT "sudo apt-get install pkg-config")
    set(LARK_MEMORY_CORE_PROTOBUF_INSTALL_HINT "sudo apt-get install protobuf-compiler libprotobuf-dev")
    set(LARK_MEMORY_CORE_GRPC_INSTALL_HINT "sudo apt-get install libgrpc++-dev libgrpc-dev protobuf-compiler-grpc")
    set(LARK_MEMORY_CORE_OPENSSL_INSTALL_HINT "sudo apt-get install libssl-dev")
    set(LARK_MEMORY_CORE_GTEST_INSTALL_HINT "sudo apt-get install libgtest-dev")
elseif(EXISTS "/etc/redhat-release")
    set(LARK_MEMORY_CORE_PKGCONFIG_INSTALL_HINT "sudo yum install pkgconfig")
    set(LARK_MEMORY_CORE_PROTOBUF_INSTALL_HINT "sudo yum install protobuf-compiler protobuf-devel")
    set(LARK_MEMORY_CORE_GRPC_INSTALL_HINT "sudo yum install grpc-devel protobuf-devel")
    set(LARK_MEMORY_CORE_OPENSSL_INSTALL_HINT "sudo yum install openssl-devel")
    set(LARK_MEMORY_CORE_GTEST_INSTALL_HINT "sudo yum install gtest-devel")
else()
    set(LARK_MEMORY_CORE_PKGCONFIG_INSTALL_HINT "install pkg-config")
    set(LARK_MEMORY_CORE_PROTOBUF_INSTALL_HINT "install protobuf compiler and development headers")
    set(LARK_MEMORY_CORE_GRPC_INSTALL_HINT "install grpc++, grpc, and grpc_cpp_plugin")
    set(LARK_MEMORY_CORE_OPENSSL_INSTALL_HINT "install OpenSSL development headers and libraries")
    set(LARK_MEMORY_CORE_GTEST_INSTALL_HINT "install GoogleTest development package")
endif()

find_package(PkgConfig QUIET)

if(NOT PKG_CONFIG_FOUND)
    message(STATUS
        "pkg-config not found; falling back to CMake/manual dependency discovery. "
        "Install it for more reliable detection: ${LARK_MEMORY_CORE_PKGCONFIG_INSTALL_HINT}"
    )
endif()

foreach(cache_var
    OPENSSL_INCLUDE_DIR
    OPENSSL_SSL_LIBRARY
    OPENSSL_CRYPTO_LIBRARY
    pkgcfg_lib__OPENSSL_ssl
    pkgcfg_lib__OPENSSL_crypto
)
    if(DEFINED ${cache_var} AND NOT EXISTS "${${cache_var}}")
        unset(${cache_var} CACHE)
    endif()
endforeach()

find_package(OpenSSL QUIET)

if(OPENSSL_FOUND AND NOT TARGET OpenSSL::Crypto AND DEFINED OPENSSL_CRYPTO_LIBRARY)
    add_library(OpenSSL::Crypto UNKNOWN IMPORTED)
    set_target_properties(OpenSSL::Crypto PROPERTIES
        IMPORTED_LOCATION "${OPENSSL_CRYPTO_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${OPENSSL_INCLUDE_DIR}"
    )
endif()

if(OPENSSL_FOUND AND NOT TARGET OpenSSL::SSL AND DEFINED OPENSSL_SSL_LIBRARY)
    add_library(OpenSSL::SSL UNKNOWN IMPORTED)
    set_target_properties(OpenSSL::SSL PROPERTIES
        IMPORTED_LOCATION "${OPENSSL_SSL_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${OPENSSL_INCLUDE_DIR}"
    )
    if(TARGET OpenSSL::Crypto)
        set_target_properties(OpenSSL::SSL PROPERTIES
            INTERFACE_LINK_LIBRARIES OpenSSL::Crypto
        )
    endif()
endif()

if(OPENSSL_FOUND)
    message(STATUS "Found OpenSSL: ${OPENSSL_SSL_LIBRARY}")
else()
    message(STATUS
        "OpenSSL development files were not found via CMake. "
        "If configure or link fails, install OpenSSL: ${LARK_MEMORY_CORE_OPENSSL_INSTALL_HINT}"
    )
endif()

set(LARK_MEMORY_CORE_MANUAL_BIN_PATHS
    /usr/bin
    /usr/local/bin
)

set(LARK_MEMORY_CORE_MANUAL_LIB_PATHS
    /usr/lib64
    /usr/local/lib64
    /usr/lib
    /usr/local/lib
    /usr/lib/x86_64-linux-gnu
)

set(LARK_MEMORY_CORE_MANUAL_INCLUDE_PATHS
    /usr/include
    /usr/local/include
)

set(PROTOBUF_OK FALSE)

if(PKG_CONFIG_FOUND)
    pkg_check_modules(PROTOBUF_PC QUIET IMPORTED_TARGET protobuf)
    if(PROTOBUF_PC_FOUND)
        set(PROTOBUF_OK TRUE)
        message(STATUS "Found Protobuf via pkg-config")
    endif()
endif()

if(NOT PROTOBUF_OK)
    find_package(Protobuf REQUIRED)
    set(PROTOBUF_OK TRUE)
    message(STATUS "Found Protobuf via CMake find_package: ${Protobuf_LIBRARIES}")
endif()

if(DEFINED Protobuf_PROTOC_EXECUTABLE AND EXISTS "${Protobuf_PROTOC_EXECUTABLE}")
    set(PROTOC_EXECUTABLE "${Protobuf_PROTOC_EXECUTABLE}")
else()
    find_program(PROTOC_EXECUTABLE
        NAMES protoc
        PATHS ${LARK_MEMORY_CORE_MANUAL_BIN_PATHS}
    )
endif()

if(NOT PROTOC_EXECUTABLE)
    message(FATAL_ERROR
        "protoc not found. Install the protobuf compiler.\n"
        "  ${LARK_MEMORY_CORE_PROTOBUF_INSTALL_HINT}"
    )
endif()

find_package(gRPC QUIET)

if(NOT gRPC_FOUND AND PKG_CONFIG_FOUND)
    pkg_check_modules(GRPCPP QUIET IMPORTED_TARGET grpc++)
    pkg_check_modules(GRPC QUIET IMPORTED_TARGET grpc)
    if(GRPCPP_FOUND)
        set(gRPC_FOUND TRUE)
        message(STATUS "Found gRPC++ via pkg-config")
    endif()
endif()

if(NOT gRPC_FOUND)
    message(STATUS "Trying to find gRPC libraries manually...")

    find_library(GRPC_LIBRARY
        NAMES grpc++
        PATHS ${LARK_MEMORY_CORE_MANUAL_LIB_PATHS}
    )

    find_library(GRPC_REFLECTION_LIBRARY
        NAMES grpc++_reflection
        PATHS ${LARK_MEMORY_CORE_MANUAL_LIB_PATHS}
    )

    find_path(GRPC_INCLUDE_DIR
        NAMES grpcpp/grpcpp.h
        PATHS ${LARK_MEMORY_CORE_MANUAL_INCLUDE_PATHS}
    )

    find_program(GRPC_CPP_PLUGIN
        NAMES grpc_cpp_plugin
        PATHS ${LARK_MEMORY_CORE_MANUAL_BIN_PATHS}
    )

    if(GRPC_LIBRARY AND GRPC_INCLUDE_DIR AND GRPC_CPP_PLUGIN)
        set(gRPC_FOUND TRUE)
        message(STATUS "Found gRPC manually: ${GRPC_LIBRARY}")
        if(GRPC_REFLECTION_LIBRARY)
            message(STATUS "Found gRPC reflection: ${GRPC_REFLECTION_LIBRARY}")
        else()
            message(STATUS "gRPC reflection library not found (optional)")
        endif()
    endif()
endif()

if(NOT gRPC_FOUND)
    message(FATAL_ERROR
        "gRPC not found. Install the C++ runtime and compiler plugin.\n"
        "  ${LARK_MEMORY_CORE_GRPC_INSTALL_HINT}"
    )
endif()

function(lark_memory_core_target_link_grpc_and_protobuf target_name)
    if(TARGET gRPC::grpc++)
        target_link_libraries(${target_name} PRIVATE
            gRPC::grpc++
        )
        if(TARGET gRPC::grpc++_reflection)
            target_link_libraries(${target_name} PRIVATE gRPC::grpc++_reflection)
        endif()
    elseif(TARGET PkgConfig::GRPCPP)
        target_link_libraries(${target_name} PRIVATE
            PkgConfig::GRPCPP
        )
    elseif(GRPC_LIBRARY)
        target_include_directories(${target_name} PRIVATE ${GRPC_INCLUDE_DIR})
        target_link_libraries(${target_name} PRIVATE ${GRPC_LIBRARY})
        if(GRPC_REFLECTION_LIBRARY)
            target_link_libraries(${target_name} PRIVATE ${GRPC_REFLECTION_LIBRARY})
        endif()
    else()
        message(FATAL_ERROR "Cannot determine how to link gRPC libraries")
    endif()

    if(TARGET PkgConfig::PROTOBUF_PC)
        target_link_libraries(${target_name} PRIVATE
            PkgConfig::PROTOBUF_PC
        )
    else()
        if(TARGET protobuf::libprotobuf)
            target_link_libraries(${target_name} PRIVATE protobuf::libprotobuf)
        else()
            target_link_libraries(${target_name} PRIVATE ${Protobuf_LIBRARIES})
        endif()
    endif()
endfunction()
