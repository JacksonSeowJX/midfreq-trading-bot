"""Strategies written natively against the mft Strategy API."""
from mft.strategies.cross_sectional import (
    CrossSectionalRanker, CrossSectionalRankerConfig, EqualWeightBasket, EqualWeightBasketConfig,
)
from mft.strategies.ma_crossover import MACrossover, MACrossoverConfig

__all__ = ["CrossSectionalRanker", "CrossSectionalRankerConfig", "EqualWeightBasket",
           "EqualWeightBasketConfig", "MACrossover", "MACrossoverConfig"]
