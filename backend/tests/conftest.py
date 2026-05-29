import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-benchmark",
        action="store_true",
        default=False,
        help="run opt-in benchmark smoke tests without requiring pytest-benchmark",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-benchmark"):
        return
    skip = pytest.mark.skip(reason="benchmark smoke tests require --run-benchmark")
    for item in items:
        if "benchmark" in item.keywords:
            item.add_marker(skip)
