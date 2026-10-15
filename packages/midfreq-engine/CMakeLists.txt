# midfreq-engine: the backtest core as a C++17 library, a pybind11 module over it, and its tests.
#
#   midfreq_engine_core   static library: market data, portfolio, risk, strategies, engine, metrics
#   _core                 the Python extension (midfreq.engine._core), built by scikit-build-core
#   engine_tests          C++ unit tests (cmake -DMIDFREQ_ENGINE_TESTS=ON; run with ctest)
#
# Results must match the Python backtester bit for bit, so floating point is kept strict: no
# fast-math, and no fused multiply-add contraction (CPython rounds every operation separately).
cmake_minimum_required(VERSION 3.18)
project(midfreq_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(MIDFREQ_ENGINE_TESTS "Build the C++ unit tests" OFF)
option(MIDFREQ_ENGINE_PYTHON "Build the Python extension" ON)

# Some macOS Command Line Tools installs leave a stub include/c++/v1 beside clang that hides the
# SDK's libc++ headers (<vector> is "not found"); use the SDK's copy when that happens.
if(APPLE)
    include(CheckIncludeFileCXX)
    check_include_file_cxx(vector MIDFREQ_HAVE_STD_HEADERS)
    if(NOT MIDFREQ_HAVE_STD_HEADERS)
        if(CMAKE_OSX_SYSROOT)
            set(_sdk "${CMAKE_OSX_SYSROOT}")
        else()
            execute_process(COMMAND xcrun --show-sdk-path OUTPUT_VARIABLE _sdk OUTPUT_STRIP_TRAILING_WHITESPACE)
        endif()
        if(EXISTS "${_sdk}/usr/include/c++/v1/vector")
            message(STATUS "libc++ headers taken from ${_sdk}")
            add_compile_options(-nostdinc++ -isystem "${_sdk}/usr/include/c++/v1")
        endif()
    endif()
endif()

add_library(midfreq_engine_core STATIC
    src/core/pymath.cpp
    src/core/market_data.cpp
    src/core/portfolio.cpp
    src/core/risk.cpp
    src/core/metrics.cpp
    src/core/engine.cpp
    src/core/batch.cpp
    src/strategies/base.cpp
    src/strategies/single_symbol.cpp
    src/strategies/regime_switch.cpp
    src/strategies/pairs.cpp
    src/strategies/cross_sectional.cpp
    src/strategies/buy_and_hold.cpp
    src/strategies/registry.cpp
)
target_include_directories(midfreq_engine_core PUBLIC include PRIVATE src)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(midfreq_engine_core PUBLIC -ffp-contract=off -fno-fast-math)
    target_compile_options(midfreq_engine_core PRIVATE -Wall -Wextra)
elseif(MSVC)
    target_compile_options(midfreq_engine_core PUBLIC /fp:precise)
endif()

find_package(Threads REQUIRED)
target_link_libraries(midfreq_engine_core PUBLIC Threads::Threads)

if(MIDFREQ_ENGINE_PYTHON)
    find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module)
    find_package(pybind11 CONFIG REQUIRED)
    pybind11_add_module(_core MODULE bindings/module.cpp)
    target_link_libraries(_core PRIVATE midfreq_engine_core)
    target_compile_definitions(_core PRIVATE MIDFREQ_ENGINE_VERSION="${SKBUILD_PROJECT_VERSION}")
    install(TARGETS _core LIBRARY DESTINATION midfreq/engine)
endif()

if(MIDFREQ_ENGINE_TESTS)
    enable_testing()
    add_executable(engine_tests tests/cpp/test_engine.cpp)
    target_link_libraries(engine_tests PRIVATE midfreq_engine_core)
    target_include_directories(engine_tests PRIVATE src)
    add_test(NAME engine_tests COMMAND engine_tests)
endif()
