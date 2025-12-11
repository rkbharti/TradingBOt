"""
SMC Enhanced Strategy Package
Implements Guardeer's complete 10-video SMC methodology
"""

from .liquidity import LiquidityDetector
from .poi import POIIdentifier
from .bias import BiasDetector
from .zones import ZoneCalculator
from .narrative import NarrativeAnalyzer

__all__ = [
    'LiquidityDetector',
    'POIIdentifier',
    'BiasDetector',
    'ZoneCalculator',
    'NarrativeAnalyzer'
]

__version__ = "2.0.0"
__author__ = "Guardeer SMC - Enhanced by Trading Bot"
