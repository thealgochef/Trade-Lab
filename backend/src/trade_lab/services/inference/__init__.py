"""Inference services: feature construction, model registry, prediction.

This package lives behind a port boundary; domain engines never import it. The
first piece implemented is the feature layer, which reads only the L1/L0
``MarketContextBuffer`` so no feature can ever observe book depth.
"""
