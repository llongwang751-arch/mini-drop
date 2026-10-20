#include "drop/json.hpp"
#include <iostream>
template <class F> void rejects(F &&f) {
  try {
    f();
  } catch (const std::invalid_argument &) {
    return;
  }
  throw std::runtime_error("Unbounded JSON accepted");
}
int main() {
  using namespace dropcore;
  if (parse_json("{\"target\":{\"pid\":7},\"metadata\":{\"pid\":8}}")["target"]
                                                                     ["pid"] !=
      7)
    throw std::runtime_error("Valid scoped keys rejected");
  rejects([] { parse_json("{\"pid\":7,\"pid\":8}"); });
  rejects([] { parse_json("{\"target\":{\"pid\":7,\"pid\":8}}"); });
  rejects(
      [] { parse_json(std::string(40, '[') + "0" + std::string(40, ']')); });
  rejects([] { parse_json("[123456789]", 4); });
  rejects([] { parse_json("{bad json}"); });
  std::cout << "JSON: byte/depth budgets and duplicate target keys rejected\n";
}
