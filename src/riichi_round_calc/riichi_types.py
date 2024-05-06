from enum import Enum
from dataclasses import dataclass
from typing import Optional

from dacite import from_dict

NUM_PLAYERS = 4
STARTING_POINT = 25000
RETURNING_POINT = 30000
RIICHI_STICK_VALUE = 1000


class Wind(Enum):
    EAST = "EAST"
    SOUTH = "SOUTH"
    WEST = "WEST"
    NORTH = "NORTH"


WIND_ORDER = list(Wind)


class TransactionType(Enum):
    DEAL_IN = "DEAL_IN"
    SELF_DRAW = "SELF_DRAW"
    DEAL_IN_PAO = "DEAL_IN_PAO"
    SELF_DRAW_PAO = "SELF_DRAW_PAO"
    NAGASHI_MANGAN = "NAGASHI_MANGAN"
    INROUND_RYUUKYOKU = "INROUND_RYUUKYOKU"


def get_empty_score_deltas() -> list[int]:
    return [0] * NUM_PLAYERS


def get_starting_score() -> list[int]:
    return [STARTING_POINT] * NUM_PLAYERS


def get_next_wind(wind: Wind) -> Wind:
    return WIND_ORDER[(WIND_ORDER.index(wind) + 1) % NUM_PLAYERS]


@dataclass
class Hand:
    fu: int
    han: int


@dataclass
class Transaction:
    transaction_type: TransactionType
    score_deltas: list[int]
    hand: Optional[Hand] = None
    pao_target: Optional[int] = None


@dataclass
class ConcludedRound:
    """A concluded Riichi Round."""

    round_wind: Wind
    round_number: int
    honba: int
    start_riichi_stick_count: int
    end_riichi_stick_count: int
    riichis: list[int]
    tenpais: list[int]
    transactions: list[Transaction]

    @staticmethod
    def from_dict(obj: dict):
        return from_dict(ConcludedRound, obj)


@dataclass
class NewRound:
    round_wind: Wind
    round_number: int
    honba: int
    start_riichi_stick_count: int

    @staticmethod
    def from_dict(obj: dict):
        return from_dict(NewRound, obj)
