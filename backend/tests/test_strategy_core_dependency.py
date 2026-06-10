def test_strategy_core_dependency_imports_platform_version() -> None:
    import strategy_core

    assert strategy_core.PLATFORM_VERSION.startswith("strategy_core_platform_")
