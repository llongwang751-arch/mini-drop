#pragma once

#include "task.h"

#include <filesystem>
#include <string>

namespace mini_drop_native {

std::string sha256_file(const std::filesystem::path& local_path);

// Resolve the exact Server-authorized object for a generated filename.  The
// Agent never constructs an object-store prefix on its own.
std::string authorized_object_key(const Task& task, const std::string& filename);

bool upload_artifact(
    const Task& task,
    const std::filesystem::path& local_path,
    const std::string& object_key,
    std::string& error);

}  // namespace mini_drop_native
