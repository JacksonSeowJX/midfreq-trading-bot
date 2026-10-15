"""Strategies written natively against the mft Strategy API."""
from midfreq.strategies.event_driven.cross_sectional import (
    CrossSectionalRanker, CrossSectionalRankerConfig, EqualWeightBasket, EqualWeightBasketConfig,
)
from midfreq.strategies.event_driven.ma_crossover import MACrossover, MACrossoverConfig

__all__ = ["CrossSectionalRanker", "CrossSectionalRankerConfig", "EqualWeightBasket",
           "EqualWeightBasketConfig", "MACrossover", "MACrossoverConfig"]
