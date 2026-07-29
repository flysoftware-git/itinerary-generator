from unittest.mock import MagicMock

from generator.nps_resolver import NPSResolver


def test_known_capitol_reef_maps_to_care_without_api_call() -> None:
    resolver = NPSResolver.__new__(NPSResolver)
    resolver.session = MagicMock()

    code = resolver.resolve("Capitol Reef National Park")

    assert code == "care"
    resolver.session.get.assert_not_called()
