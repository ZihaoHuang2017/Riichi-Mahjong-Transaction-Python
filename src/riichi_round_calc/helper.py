from .riichi_types import Transaction, TransactionType


def containing_any(
    transactions: list[Transaction], transaction_type: TransactionType
) -> Transaction or None:
    for transaction in transactions:
        if transaction.transaction_type == transaction_type:
            return transaction
    return None
