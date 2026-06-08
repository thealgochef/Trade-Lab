def test_strategy_core_dependency_imports_engine_version() -> None:
    import strategy_core

    assert strategy_core.ENGINE_VERSION.startswith("strategy_core_engine_")
