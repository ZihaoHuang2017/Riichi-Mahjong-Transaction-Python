from .riichi_types import (
    ConcludedRound,
    NewRound,
    NUM_PLAYERS,
    get_next_wind,
    TransactionType,
    Wind,
    RETURNING_POINT,
    get_starting_score,
)

from .riichi_round import generate_overall_score_deltas


def get_new_honba_count(transactions, dealer_index, honba):
    if len(transactions) == 0:
        return honba + 1
    for transaction in transactions:
        if transaction.transaction_type in [
            TransactionType.INROUND_RYUUKYOKU,
            TransactionType.NAGASHI_MANGAN,
        ]:
            return honba + 1
        if transaction.score_deltas[dealer_index] > 0:
            return honba + 1
    return 0


def dealership_retains(transactions, tenpais, dealer_index):
    for transaction in transactions:
        if (
            transaction.transaction_type != TransactionType.NAGASHI_MANGAN
            and transaction.score_deltas[dealer_index] > 0
        ):
            return True
        if transaction.transaction_type == TransactionType.INROUND_RYUUKYOKU:
            return True
    return dealer_index in tenpais


def generate_next_round(concluded_round: ConcludedRound):
    new_honba_count = get_new_honba_count(
        concluded_round.transactions,
        concluded_round.round_number - 1,
        concluded_round.honba,
    )
    if dealership_retains(
        concluded_round.transactions,
        concluded_round.tenpais,
        concluded_round.round_number - 1,
    ):
        return NewRound(
            honba=new_honba_count,
            round_number=concluded_round.round_number,
            round_wind=concluded_round.round_wind,
            start_riichi_stick_count=concluded_round.end_riichi_stick_count,
        )
    return NewRound(
        honba=new_honba_count,
        round_number=1
        if concluded_round.round_number == NUM_PLAYERS
        else concluded_round.round_number + 1,
        round_wind=get_next_wind(concluded_round.round_wind)
        if concluded_round.round_number == NUM_PLAYERS
        else concluded_round.round_wind,
        start_riichi_stick_count=concluded_round.end_riichi_stick_count,
    )


def is_game_end(
    new_round: NewRound, concluded_rounds: list[ConcludedRound], starting_score=None
):
    if starting_score is None:
        starting_score = get_starting_score()
    if new_round.round_wind == Wind.NORTH:
        # ends at north regardless of what happens
        return True

    total_score = starting_score
    for concluded_round in concluded_rounds:
        overall_score_deltas = generate_overall_score_deltas(concluded_round)
        for i in range(NUM_PLAYERS):
            total_score[i] += overall_score_deltas[i]

    exceeds_hanten = False
    for score in total_score:
        if score < 0:
            return True
        if score >= RETURNING_POINT:
            exceeds_hanten = True

    if not exceeds_hanten:
        return False

    # At least one person is more than 30k
    if new_round.round_wind == Wind.WEST:
        return True  # dealership gone; someone's more than 30k

    last_round = concluded_rounds[-1]
    if last_round.round_wind != Wind.SOUTH or last_round.round_number != NUM_PLAYERS:
        return False  # not even S4 yet

    for i in range(len(total_score) - 1):
        if total_score[i] >= total_score[-1]:
            return False

    return True
