#pragma once

#include "config.h"

#include <filesystem>
#include <string>

namespace mini_drop_native {

std::string sha256_file(const std::filesystem::path& local_path);

bool upload_artifact(
    const Config& config,
    const std::filesystem::path& local_path,
    const std::string& object_key,
    std::string& error);

}  // namespace mini_drop_native
