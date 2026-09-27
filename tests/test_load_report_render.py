from scripts.render_load_report import html


def report():
    return {"status": "INVALID", "sha256": "a" * 64,
            "stages": [{"name": "step-1", "rate": 80, "duration_seconds": 15,
                        "summary": {"offered": 1200, "sent": 0, "unsent": 1200,
                                    "p95_ms": None, "p99_ms": None, "success_rate": 0,
                                    "quality_rate": 0, "status": "INVALID",
                                    "reasons": ["CLIENT_INFLIGHT_LIMIT"]}}]}


def test_invalid_results_missing_latency_and_unsent_requests_stay_visible():
    document = html(report(), b"test-image")
    assert "<strong>INVALID</strong>" in document
    assert "1200 / 0 / 1200" in document
    assert "CLIENT_INFLIGHT_LIMIT" in document
    assert "<td>未采集</td>" in document
    assert "该历史报告未采集目标进程资源" in document


def test_embedded_report_escapes_untrusted_strings_and_has_no_external_assets():
    data = report()
    data["stages"][0]["name"] = '<script>alert("x")</script>'
    document = html(data, b"test-image")
    assert '<script>' not in document
    assert '&lt;script&gt;' in document
    assert 'src="data:image/png;base64,' in document
    assert 'src="https://' not in document and '<script src=' not in document


def test_resource_units_growth_and_missing_observations_are_explicit():
    data = report()
    data["resources"] = {"status": "GROWTH_DETECTED", "soak_samples": 20, "error_samples": 1,
                         "handle_kind": "windows_handles", "soak_trends": {"rss_bytes": {
                             "first_third_median": 1024, "last_third_median": 2048,
                             "growth": 1024, "growth_limit": 500}}}
    document = html(data, b"test-image")
    for value in ['GROWTH_DETECTED', 'windows_handles', '缺失 1', 'rss_bytes', '2,048.00']:
        assert value in document
