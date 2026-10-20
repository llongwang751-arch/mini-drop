// 特权helper的独立进程入口；实际协议、准入预算和恢复实现集中在helper.cpp。
// 单独部署使普通Agent无需持有采集内核权限，也不需要共享长期存储凭据。

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
