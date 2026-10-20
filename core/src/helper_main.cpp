#include "drop/config.hpp"
#include "drop/helper.hpp"
#include "drop/process.hpp"
#include <curl/curl.h>

int main(int argc, char **argv) {
  curl_global_init(CURL_GLOBAL_DEFAULT);
  try {
    dropcore::initialize_configuration(argc, argv);
    const auto result = dropcore::run_collector_helper();
    curl_global_cleanup();
    return result;
  } catch (const std::exception &) {
    dropcore::log("drop-collector-helper",
                  "Startup or bounded local service failed", "",
                  "CONFIGURATION_ERROR");
    curl_global_cleanup();
    return 1;
  }
}
