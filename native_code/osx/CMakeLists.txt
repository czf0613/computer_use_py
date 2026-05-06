cmake_minimum_required(VERSION 3.20)
project(scapkit_computer_use_osx C)

# This CMakeLists.txt is only for IDE code hints (e.g. CLion, VS Code).
# Do NOT use CMake to build this project. Use setup.py instead.

find_package(Python3 3.14 REQUIRED COMPONENTS Development)

add_library(_scapkit SHARED
    src/ext.c
    src/display.c
    src/control.c
)

target_include_directories(_scapkit PRIVATE
    ${Python3_INCLUDE_DIRS}
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(_scapkit PRIVATE
    ${Python3_LIBRARIES}
    "-framework CoreGraphics"
)
