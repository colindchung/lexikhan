from unittest.mock import Mock

import pytest
import smoke_backend


def test_smoke_refuses_production_before_creating_test_data(monkeypatch):
    monkeypatch.setattr(smoke_backend, "outputs_for", lambda *args: {"Stage": "prod"})
    session = Mock()
    with pytest.raises(ValueError, match="only run against Lexikhan-dev"):
        smoke_backend.run(session, "Lexikhan-prod")
    session.client.assert_not_called()
