cmake_minimum_required(VERSION 3.16)
project(mini_drop_native_control LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(GRPCPP REQUIRED grpc++)
pkg_check_modules(PROTOBUF REQUIRED protobuf)
pkg_check_modules(PQXX REQUIRED libpqxx)

set(GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${GENERATED_DIR}")
set(PROTO_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../proto")
set(PROTO_FILES common.proto hotmethod.proto healthcheck.proto init.proto)
set(GENERATED_SOURCES)
foreach(PROTO_FILE IN LISTS PROTO_FILES)
  get_filename_component(PROTO_NAME "${PROTO_FILE}" NAME_WE)
  list(APPEND GENERATED_SOURCES
    "${GENERATED_DIR}/${PROTO_NAME}.pb.cc"
    "${GENERATED_DIR}/${PROTO_NAME}.grpc.pb.cc")
endforeach()

add_custom_command(
  OUTPUT ${GENERATED_SOURCES}
  COMMAND /usr/bin/protoc -I ${PROTO_DIR} -I /usr/include
    --cpp_out=${GENERATED_DIR}
    --grpc_out=${GENERATED_DIR}
    --plugin=protoc-gen-grpc=/usr/bin/grpc_cpp_plugin
    ${PROTO_FILES}
  WORKING_DIRECTORY "${PROTO_DIR}"
  DEPENDS
    "${PROTO_DIR}/common.proto"
    "${PROTO_DIR}/hotmethod.proto"
    "${PROTO_DIR}/healthcheck.proto"
    "${PROTO_DIR}/init.proto"
  VERBATIM)

add_executable(mini-drop-native-control src/main.cpp ${GENERATED_SOURCES})
target_include_directories(mini-drop-native-control PRIVATE
  "${GENERATED_DIR}"
  ${GRPCPP_INCLUDE_DIRS} ${PROTOBUF_INCLUDE_DIRS} ${PQXX_INCLUDE_DIRS})
target_link_directories(mini-drop-native-control PRIVATE
  ${GRPCPP_LIBRARY_DIRS} ${PROTOBUF_LIBRARY_DIRS} ${PQXX_LIBRARY_DIRS})
target_compile_options(mini-drop-native-control PRIVATE
  -Wall -Wextra -Wpedantic
  ${GRPCPP_CFLAGS_OTHER} ${PROTOBUF_CFLAGS_OTHER} ${PQXX_CFLAGS_OTHER})
target_link_libraries(mini-drop-native-control PRIVATE
  ${GRPCPP_LIBRARIES} ${PROTOBUF_LIBRARIES} ${PQXX_LIBRARIES} pthread)
