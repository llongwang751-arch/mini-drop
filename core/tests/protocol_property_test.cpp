// 固定种子的合成协议变异，不执行命令、不连接 Agent，也不把随机数据当真实 profile。
// 直接调用生产 parse_json 和安全标识校验；异常由解析器边界捕获，内存错误由消毒器门禁捕获。
#include "drop/domain.hpp"
#include "drop/json.hpp"
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

namespace {
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
void must_reject(const std::string &raw, std::size_t maximum = 2048) {
  try {
    static_cast<void>(dropcore::parse_json(raw, maximum));
  } catch (const std::invalid_argument &) {
    return;
  }
  throw std::runtime_error("Malformed or over-budget JSON accepted");
}
} // 结束匿名命名空间

int main() {
  constexpr std::uint32_t seed = 20261023;
  std::mt19937 random(seed);
  const std::string original = R"({"target":{"pid":7},"safe":true})";
  for (std::size_t iteration = 0; iteration < 4096; ++iteration) {
    std::string raw = original;
    const auto mutations = 1 + random() % 8;
    for (std::size_t index = 0; index < mutations; ++index) {
      const auto position = random() % raw.size();
      raw[position] = static_cast<char>(random() % 256);
    }
    try {
      const auto parsed = dropcore::parse_json(raw, 2048);
      const auto canonical = parsed.dump();
      require(dropcore::parse_json(canonical) == parsed,
              "Parsed JSON did not round-trip");
    } catch (const std::invalid_argument &) {
      // 拒绝非法输入是正常结果，不能把崩溃或越界访问吞成合法拒绝。
    }
    const auto id = "synthetic_" + std::to_string(iteration);
    require(dropcore::safe_identifier(id), "Safe identifier rejected");
    require(!dropcore::safe_identifier(id + "/../other"),
            "Path traversal escaped identifier boundary");
    must_reject("{\"same\":" + std::to_string(iteration) +
                ",\"same\":false}");
  }
  for (std::size_t depth = 34; depth < 66; ++depth)
    must_reject(std::string(depth, '[') + "0" + std::string(depth, ']'));
  must_reject(std::string(2049, ' '));
  must_reject("{\"" + std::string(257, 'k') + "\":1}");
  require(!dropcore::safe_identifier(std::string(129, 'a')),
          "Overlong identifier accepted");
  std::cout << "synthetic protocol properties: seed=" << seed
            << " mutations=4096 duplicate_keys=4096 depth_cases=32 passed\n";
}
