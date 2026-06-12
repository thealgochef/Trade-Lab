"""Contract-ordered feature construction from the L1/L0 market-context buffer.

Every feature function reads only :class:`MarketContextBuffer` (trades + best
bid/ask), so book depth is structurally unreadable. Semantics are ported from
Claude-Quant-Lab's ``dashboard_utility_builder`` / ``experiment.features`` but
re-implemented against Trade-Lab's integer-tick domain, with each feature's own
empty-case rule preserved exactly (absorption -> 0.0; approach -> NaN).
"""

from trade_lab.services.inference.features.feature_functions import (
    DEFAULT_FEATURE_REGISTRY,
    FeatureComputationError,
    FeatureFn,
    FeatureFunctionRegistry,
    FeatureWindow,
    LevelContext,
    LevelDirection,
    build_feature_vector,
)

__all__ = [
    "DEFAULT_FEATURE_REGISTRY",
    "FeatureComputationError",
    "FeatureFn",
    "FeatureFunctionRegistry",
    "FeatureWindow",
    "LevelContext",
    "LevelDirection",
    "build_feature_vector",
]
