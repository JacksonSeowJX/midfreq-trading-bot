"""Strategies written natively against the event-driven Strategy API (midfreq.strategies.trading)."""
from midfreq.strategies.event_driven.cross_sectional import (
    CrossSectionalRanker, CrossSectionalRankerConfig, EqualWeightBasket, EqualWeightBasketConfig,
)
from midfreq.strategies.event_driven.ma_crossover import MACrossover, MACrossoverConfig

__all__ = ["CrossSectionalRanker", "CrossSectionalRankerConfig", "EqualWeightBasket",
           "EqualWeightBasketConfig", "MACrossover", "MACrossoverConfig"]
