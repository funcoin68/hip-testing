from lighter_testing import LIGHTER_TESTNET


def test_repo_is_testnet_only() -> None:
    """The single most important invariant of this repo."""
    assert LIGHTER_TESTNET is True
