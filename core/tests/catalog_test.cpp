#include "drop/catalog.hpp"
#include <iostream>
#include <stdexcept>
template <class F> void rejects(F &&f) {
  try {
    f();
  } catch (const std::exception &) {
    return;
  }
  throw std::runtime_error("Unsafe parameters accepted");
}
int main(int argc, char **argv) {
  if (argc != 2)
    return 2;
  dropcore::Catalog c(argv[1]);
  c.validate(
      "JAVA_CPU",
      {{"pid", 100}, {"duration_seconds", 5}, {"options", {{"event", "cpu"}}}});
  rejects([&] {
    c.validate("JAVA_CPU",
               {{"pid", 100}, {"options", {{"event", "cpu; touch /tmp/pwn"}}}});
  });
  rejects([&] {
    c.validate("SCRIPT", {{"pid", 100},
                          {"options", {{"template_id", "../../etc/shadow"}}}});
  });
  rejects([&] {
    c.validate("GO_CPU", {{"pid", 100}, {"options", {{"port", 99999}}}});
  });
  rejects([&] {
    c.validate("PYTHON_CPU", {{"pid", 100}, {"options", {{"argv", "bash"}}}});
  });
  rejects([&] {
    c.validate("RESOURCE_SNAPSHOT",
               {{"target", {{"type", "host"}}},
                {"budget", {{"max_file_bytes", 9999999999LL}}}});
  });
  rejects([&] { c.validate("COMPOSITE", {}); });
  c.validate("GO_HEAP", {{"pid", 100},
                         {"options",
                          {{"sample_type", "alloc_space"},
                           {"label_filter", {{"tenant", "fixture"}}}}}});
  rejects([&] {
    auto labels = nlohmann::json::object();
    for (int i = 0; i < 33; ++i)
      labels[std::to_string(i)] = "v";
    c.validate("GO_HEAP",
               {{"pid", 100}, {"options", {{"label_filter", labels}}}});
  });
  rejects([&] { c.validate("JAVA_CPU", {{"target", {{"type", "host"}}}}); });
  if (c.entries().size() != 18)
    throw std::runtime_error("TaskKind catalog incomplete");
  std::cout << "catalog: all 18 kinds and injection/schema boundaries passed\n";
}
