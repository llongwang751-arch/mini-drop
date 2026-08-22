from scripts.run_official_campaign import wait_campaign


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"data": {"run_id": "campaign-1", "status": "COMPLETED"}}


class _AuthenticatedSession:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, timeout: int):
        self.urls.append(url)
        return _Response()


def test_wait_campaign_reuses_authenticated_session() -> None:
    session = _AuthenticatedSession()

    result = wait_campaign(session, "https://control.example", "campaign-1", 1)

    assert result["status"] == "COMPLETED"
    assert session.urls == [
        "https://control.example/api/v1/diagnosis-campaigns/runs/campaign-1"
    ]
