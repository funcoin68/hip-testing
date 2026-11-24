from hip_testing import HIP_TESTNET


def test_repo_is_testnet_only() -> None:
    """The single most important invariant of this repo."""
    assert HIP_TESTNET is True
