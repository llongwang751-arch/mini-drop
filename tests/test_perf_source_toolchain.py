"""Toolchain text parsers reject lookalikes; these are not live profiling tests."""
import pytest

from scripts.verify_perf_source_toolchain import parse_hotspot_symbol, parse_source_mapping


@pytest.mark.parametrize("symbol", ["cpp_cpu_hot_function", "cpp_cpu_hot_function(unsigned long)"])
def test_real_extern_c_and_complete_demangled_symbols_keep_exact_name_and_address(symbol):
    row = f"000000000040EDC0 T {symbol}"
    assert parse_hotspot_symbol("0000000000401000 T main\n" + row + "\n") == (
        "000000000040EDC0", symbol, row
    )


@pytest.mark.parametrize("row", [
    "0000000000000000 T cpp_cpu_hot_function",
    "000000000040edc0 D cpp_cpu_hot_function",
    "000000000040edc0 W cpp_cpu_hot_function",
    "                 U cpp_cpu_hot_function",
    "000000000040edc0 T cpp_cpu_hot_function_extra",
    "000000000040edc0 T cpp_cpu_hot_function+0x10",
    "000000000040edc0 T cpp_cpu_hot_function(unsigned long) [clone .cold]",
    "000000000040edc0 T unrelated::cpp_cpu_hot_function(unsigned long)",
    "000000000040edc0 T cpp_cpu_hot_function(unsigned long",
    "000000000040edc0 T cpp_cpu_hot_function)junk(",
    "gggggggggggggggg T cpp_cpu_hot_function",
])
def test_missing_or_lookalike_hotspot_cannot_supply_a_source_address(row):
    with pytest.raises(RuntimeError, match="one real"):
        parse_hotspot_symbol(row + "\n")


def test_ambiguous_overloaded_hotspot_addresses_are_not_guessed():
    with pytest.raises(RuntimeError, match="one real"):
        parse_hotspot_symbol(
            "000000000040edc0 T cpp_cpu_hot_function\n"
            "000000000040fdc0 T cpp_cpu_hot_function(unsigned long)\n"
        )


@pytest.mark.parametrize("symbol", ["cpp_cpu_hot_function", "cpp_cpu_hot_function(unsigned long)"])
def test_addr2line_keeps_actual_positive_source_mapping_without_inventing_a_line(symbol):
    assert parse_source_mapping(symbol + "\n/src/main.cpp:33\n", symbol) == ("/src/main.cpp:33", 33)


def test_actual_discriminator_suffix_is_removed_without_changing_the_measured_line():
    assert parse_source_mapping(
        "cpp_cpu_hot_function\nmain.cpp:38 (discriminator 4)\n", "cpp_cpu_hot_function"
    ) == ("main.cpp:38", 38)


@pytest.mark.parametrize("mapped", [
    "??\n??:0\n",
    "cpp_cpu_hot_function\nmain.cpp:0\n",
    "cpp_cpu_hot_function\nmain.cpp:-1\n",
    "cpp_cpu_hot_function\nother.cpp:33\n",
    "cpp_cpu_hot_function_extra\nmain.cpp:33\n",
    "cpp_cpu_hot_function(unsigned long)\nmain.cpp:33\n",
    "cpp_cpu_hot_function\nmain.cpp:33\ncpp_cpu_hot_function\nmain.cpp:34\n",
    "cpp_cpu_hot_function\nmain.cpp:33 (discriminator unknown)\n",
    "cpp_cpu_hot_function\nmain.cpp:nan\n",
])
def test_unresolved_changed_function_or_invalid_source_line_is_rejected(mapped):
    with pytest.raises(RuntimeError, match="addr2line"):
        parse_source_mapping(mapped, "cpp_cpu_hot_function")
