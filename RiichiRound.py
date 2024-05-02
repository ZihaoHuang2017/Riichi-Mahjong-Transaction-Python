from Types import (
    NewRound,
    get_empty_score_deltas,
    Transaction,
    TransactionType,
    NUM_PLAYERS,
    ConcludedRound,
    Hand,
    RIICHI_STICK_VALUE,
)
from Points import calculate_hand_value, MANGAN_BASE_POINT
from Helper import containing_any


def get_deal_in_multiplier(person_index: int, dealer_index: int) -> int:
    return 6 if person_index == dealer_index else 4


def get_self_draw_multiplier(person_index: int, dealer_index: int, is_dealer: bool):
    if is_dealer or person_index == dealer_index:
        return 2
    return 1


def get_deal_in_transaction(
    winner_index: int, loser_index: int, dealer_index: int, hand: Hand
) -> Transaction:
    score_deltas = get_empty_score_deltas()
    multiplier = get_deal_in_multiplier(winner_index, dealer_index)
    hand_value = calculate_hand_value(multiplier, hand)
    score_deltas[winner_index] -= hand_value
    score_deltas[loser_index] += hand_value
    return Transaction(TransactionType.DEAL_IN, score_deltas, hand)


def get_self_draw_transaction(
    winner_index: int, dealer_index: int, hand: Hand
) -> Transaction:
    score_deltas = get_empty_score_deltas()
    is_dealer = winner_index == dealer_index
    total_score = 0
    for i in range(NUM_PLAYERS):
        if i != winner_index:
            value = calculate_hand_value(
                get_self_draw_multiplier(i, dealer_index, is_dealer), hand
            )
            total_score += value
            score_deltas[i] = -value
    score_deltas[winner_index] = total_score
    return Transaction(TransactionType.SELF_DRAW, score_deltas, hand)


def get_in_round_ryuukyoku_transaction() -> Transaction:
    return Transaction(TransactionType.INROUND_RYUUKYOKU, get_empty_score_deltas())


def get_nagashi_mangan_transaction(winner_index: int, dealer_index: int) -> Transaction:
    score_deltas = get_empty_score_deltas()
    is_dealer = winner_index == dealer_index
    for i in range(NUM_PLAYERS):
        if i != winner_index:
            value = MANGAN_BASE_POINT * get_self_draw_multiplier(
                i, dealer_index, is_dealer
            )
            score_deltas[i] = -value
            score_deltas[winner_index] += value
    return Transaction(TransactionType.NAGASHI_MANGAN, score_deltas)


def get_deal_in_pao_transaction(
    winner_index, deal_in_person_index, pao_player_index, dealer_index, hand
) -> Transaction:
    score_deltas = get_empty_score_deltas()
    multiplier = get_deal_in_multiplier(winner_index, dealer_index)
    score_deltas[deal_in_person_index] = -calculate_hand_value(multiplier // 2, hand)
    score_deltas[pao_player_index] = -calculate_hand_value(multiplier // 2, hand)
    score_deltas[winner_index] = calculate_hand_value(multiplier, hand)
    return Transaction(
        TransactionType.DEAL_IN_PAO, score_deltas, hand, pao_player_index
    )


def get_self_draw_pao_transaction(
    winner_index, pao_player_index, dealer_index, hand: Hand
) -> Transaction:
    score_deltas = get_empty_score_deltas()
    multiplier = get_deal_in_multiplier(winner_index, dealer_index)
    value = calculate_hand_value(multiplier, hand)
    score_deltas[pao_player_index] = -value
    score_deltas[winner_index] = value
    return Transaction(
        TransactionType.SELF_DRAW_PAO, score_deltas, hand, pao_player_index
    )


def find_head_bump_winner(transactions: list[Transaction]) -> int:
    winners = set()
    losers = set()
    for transaction in transactions:
        for i in range(NUM_PLAYERS):
            if transaction.paoTarget == i:
                continue
            if transaction.score_deltas[i] > 0:
                winners.add(i)
            elif transaction.score_deltas[i] < 0:
                losers.add(i)
    # Two cases:
    # Either tsumo, in which case there's only one winner
    # Or it's a double/triple ron, in which case there's only one loser

    [loser, *_] = losers
    return get_closest_winner(loser, winners)


def get_closest_winner(loser_local_pos: int, winners: set[int]) -> int:
    [closest_winner, *_] = winners
    for winner in winners:
        if (winner - loser_local_pos) % NUM_PLAYERS < (
            closest_winner - loser_local_pos
        ) % NUM_PLAYERS:
            closest_winner = winner
    return closest_winner


def generate_tenpai_score_deltas(tenpais: list[int]) -> list[int]:
    score_deltas = get_empty_score_deltas()
    if not tenpais or len(tenpais) == NUM_PLAYERS:
        return score_deltas
    for i in range(NUM_PLAYERS):
        if i in tenpais:
            score_deltas[i] = 3000 // len(tenpais)
        else:
            score_deltas[i] = -3000 // len(tenpais)
    return score_deltas


def reduce_score_deltas(transactions: list[Transaction]) -> list[int]:
    result = get_empty_score_deltas()
    for transaction in transactions:
        for i in range(NUM_PLAYERS):
            result[i] += transaction.score_deltas[i]
    return result


def generate_overall_score_deltas(concluded_round: ConcludedRound) -> list[int]:
    raw_score_deltas = reduce_score_deltas(concluded_round.transactions)
    for riichi_player in concluded_round.riichis:
        raw_score_deltas[riichi_player] -= 1000
    if containing_any(concluded_round.transactions, TransactionType.NAGASHI_MANGAN):
        return raw_score_deltas
    riichi_deltas = [
        a + b
        for a, b in zip(
            generate_tenpai_score_deltas(concluded_round.tenpais), raw_score_deltas
        )
    ]
    headbump_winner = find_head_bump_winner(concluded_round.transactions)
    if concluded_round.end_riichi_stick_count == 0:
        riichi_deltas[headbump_winner] += (
            concluded_round.start_riichi_stick_count + len(concluded_round.riichis)
        ) * RIICHI_STICK_VALUE
    return riichi_deltas


def transform_transactions(
    transactions: list[Transaction], honba: int
) -> list[Transaction]:
    if not transactions:
        return []
    transaction = determine_honba_transaction(transactions)
    new_transaction = add_honba(transaction, honba)
    for i in range(NUM_PLAYERS):
        if transactions[i] == transaction:
            transactions[i] = new_transaction
            break
    return transactions


def determine_honba_transaction(transactions: list[Transaction]) -> Transaction:
    if len(transactions) == 1:
        return transactions[0]
    potential_tsumo = containing_any(transactions, TransactionType.SELF_DRAW)
    if potential_tsumo:
        return potential_tsumo
    headbump_winner = find_head_bump_winner(transactions)
    for transaction in transactions:
        if (
            transaction.score_deltas[headbump_winner] > 0
            and transaction.transaction_type == TransactionType.DEAL_IN_PAO
        ):
            return transaction
    for transaction in transactions:
        if transaction.score_deltas[headbump_winner] > 0:
            return transaction


def add_honba(transaction: Transaction, honba: int) -> Transaction:
    new_transaction = Transaction(
        transaction.transaction_type,
        transaction.score_deltas,
        transaction.hand,
        transaction.paoTarget,
    )
    for i in range(NUM_PLAYERS):
        new_transaction.score_deltas[i] = transaction.score_deltas[i]
    match new_transaction.transaction_type:
        case TransactionType.NAGASHI_MANGAN, TransactionType.INROUND_RYUUKYOKU:
            pass
        case TransactionType.SELF_DRAW:
            for i in range(NUM_PLAYERS):
                if new_transaction.score_deltas[i] > 0:
                    new_transaction.score_deltas[i] += 300 * honba
                else:
                    new_transaction.score_deltas[i] = -100 * honba
        case TransactionType.DEAL_IN, TransactionType.DEAL_IN_PAO:
            handle_deal_in(new_transaction, honba)
        case TransactionType.SELF_DRAW_PAO:
            for i in range(NUM_PLAYERS):
                if new_transaction.score_deltas[i] > 0:
                    new_transaction.score_deltas[i] += 300 * honba
                elif new_transaction.score_deltas[i] < 0:
                    new_transaction.score_deltas[i] = -300 * honba
    return new_transaction


def handle_deal_in(transaction: Transaction, honba: int) -> None:
    for i in range(NUM_PLAYERS):
        if transaction.paoTarget == i:
            continue
        if transaction.score_deltas[i] > 0:
            transaction.score_deltas[i] += 300 * honba
        elif transaction.score_deltas[i] < 0:
            transaction.score_deltas[i] = -300 * honba


class RiichiRound:
    def __init__(self, new_round: NewRound):
        self.round_wind = new_round.round_wind
        self.round_number = new_round.round_number
        self.honba = new_round.honba
        self.start_riichi_stick_count = new_round.start_riichi_stick_count
        self.riichis = []
        self.tenpais = []
        self.transactions = []
        self.dealer_index = self.round_number - 1

    def add_deal_in(self, winner_index, loser_index, hand):
        self.transactions.append(
            get_deal_in_transaction(winner_index, loser_index, self.dealer_index, hand)
        )

    def add_self_draw(self, winner_index, hand):
        self.transactions.append(
            get_self_draw_transaction(winner_index, self.dealer_index, hand)
        )

    def add_nagashi_mangan(self, winner_index):
        self.transactions.append(
            get_nagashi_mangan_transaction(winner_index, self.dealer_index)
        )

    def add_deal_in_pao(
        self, winner_index, deal_in_person_index, pao_person_index, hand
    ):
        self.transactions.append(
            get_deal_in_pao_transaction(
                winner_index,
                deal_in_person_index,
                pao_person_index,
                self.dealer_index,
                hand,
            )
        )

    def add_self_draw_pao(self, winner_index, pao_person_index, hand):
        self.transactions.append(
            get_self_draw_pao_transaction(
                winner_index, pao_person_index, self.dealer_index, hand
            )
        )

    def add_inround_ryuukyoku(self):
        self.transactions.append(get_in_round_ryuukyoku_transaction())

    def set_tenpais(self, tenpais: list[int]):
        self.tenpais = tenpais

    def set_riichis(self, riichis: list[int]):
        self.riichis = riichis

    def get_final_riichi_sticks(self):
        for transaction in self.transactions:
            if transaction.transactionType in [
                TransactionType.DEAL_IN,
                TransactionType.SELF_DRAW,
                TransactionType.SELF_DRAW_PAO,
                TransactionType.DEAL_IN_PAO,
            ]:
                return 0
        return self.start_riichi_stick_count + len(self.riichis)

    def concludeRound(self) -> ConcludedRound:
        return ConcludedRound(
            round_number=self.round_number,
            round_wind=self.round_wind,
            honba=self.honba,
            start_riichi_stick_count=self.start_riichi_stick_count,
            riichis=self.riichis,
            tenpais=self.tenpais,
            end_riichi_stick_count=self.get_final_riichi_sticks(),
            transactions=transform_transactions(self.transactions, self.honba),
        )
