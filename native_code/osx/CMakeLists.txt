cmake_minimum_required(VERSION 3.20)
set(CMAKE_OSX_DEPLOYMENT_TARGET "12.3" CACHE STRING "Source build macOS deployment target")
project(scapkit_computer_use_osx C OBJC)

# This CMakeLists.txt is only for IDE code hints (e.g. CLion, VS Code).
# Do NOT use CMake to build this project. Use setup.py instead.

find_package(Python3 3.10 REQUIRED COMPONENTS Development)

add_library(_scapkit SHARED
    src/ext.c
    src/display.c
    src/control.c
    src/capture.m
)

target_compile_options(_scapkit PRIVATE -fobjc-arc)
target_compile_definitions(_scapkit PRIVATE PY_SSIZE_T_CLEAN)

target_include_directories(_scapkit PRIVATE
    ${Python3_INCLUDE_DIRS}
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(_scapkit PRIVATE
    ${Python3_LIBRARIES}
    "-framework Foundation"
    "-framework UniformTypeIdentifiers"
    "-framework CoreGraphics"
    "-framework ApplicationServices"
    "-framework ScreenCaptureKit"
    "-framework CoreMedia"
    "-framework CoreVideo"
    "-framework CoreImage"
    "-framework ImageIO"
)
