cmake_minimum_required(VERSION 3.16)
project(mini_drop_gperftools_bridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(mini-drop-gperftools-bridge src/main.cpp)
target_compile_options(mini-drop-gperftools-bridge PRIVATE -Wall -Wextra -Wpedantic)
install(TARGETS mini-drop-gperftools-bridge RUNTIME DESTINATION bin)
