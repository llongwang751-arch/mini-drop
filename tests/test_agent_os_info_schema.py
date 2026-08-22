from sqlalchemy import Text

from server.app.models import AgentModel


def test_agent_os_info_accepts_structured_compatibility_report() -> None:
    """Agent registration must not impose the obsolete 256-byte ceiling."""

    assert isinstance(AgentModel.__table__.c.os_info.type, Text)
