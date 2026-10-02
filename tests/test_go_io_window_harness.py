import pytest

from scripts.verify_go_io_window import window_average


def test_io_window_uses_new_operations_instead_of_the_lifetime_mean():
    assert window_average({'io_operations': 100, 'io_operation_duration_ms_total': 10000},
                          {'io_operations': 110, 'io_operation_duration_ms_total': 10100}) == 10


@pytest.mark.parametrize('count,duration', [(100, 10100), (99, 10100), (110, 9999), (110, float('nan'))])
def test_missing_operations_resets_and_nonfinite_time_cannot_pass_fixture_verification(count, duration):
    with pytest.raises(ValueError):
        window_average({'io_operations': 100, 'io_operation_duration_ms_total': 10000},
                       {'io_operations': count, 'io_operation_duration_ms_total': duration})
