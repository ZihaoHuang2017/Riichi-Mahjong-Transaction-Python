import math
from Types import Hand

MANGAN_BASE_POINT = 2000


def mangan_value(points: int) -> int:
    multiplier = 0
    if points == 5:
        multiplier = 1
    elif points <= 7:
        multiplier = 1.5
    elif points <= 10:
        multiplier = 2
    elif points <= 12:
        multiplier = 3
    # After 13 points is hit, we only see multiples of 13
    elif points == 13:
        multiplier = 4
    elif points == 26:
        multiplier = 4 * 2
    elif points == 39:
        multiplier = 4 * 3
    elif points == 52:
        multiplier = 4 * 4
    elif points == 65:
        multiplier = 4 * 5
    return MANGAN_BASE_POINT * multiplier


def calculate_hand_value(multiplier: int, hand: Hand):
    fu, han = hand.fu, hand.han
    if han >= 5:
        return mangan_value(han) * multiplier
    mangan_payout = MANGAN_BASE_POINT * multiplier
    hand_value = math.ceil((fu * (2 ** (2 + han)) * multiplier) // 100) * 100
    return mangan_payout if hand_value > mangan_payout else hand_value
