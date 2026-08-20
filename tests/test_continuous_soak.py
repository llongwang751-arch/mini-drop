from scripts.continuous_soak import items_of


def test_items_of_accepts_api_page():
    assert items_of({"items": [{"id": "one"}], "total": 1}) == [{"id": "one"}]


def test_items_of_accepts_plain_list():
    assert items_of([{"id": "one"}]) == [{"id": "one"}]


def test_items_of_rejects_unexpected_shape():
    assert items_of({"data": []}) == []
