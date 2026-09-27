"""Execution outcomes that must retain the transaction latch."""


class UncertainTransaction(RuntimeError):
    pass


class SimulationRejected(RuntimeError):
    """A contract revert during estimateGas, before this transaction is signed."""

    def __init__(self, provider_message: str) -> None:
        reasons = {
            "PancakeRouter: INSUFFICIENT_OUTPUT_AMOUNT": "выход ниже minOut",
            "PancakeRouter: EXPIRED": "истёк срок транзакции",
            "TransferHelper: TRANSFER_FROM_FAILED": "контракт отказал в переводе входного токена",
            "TransferHelper: TRANSFER_FAILED": "контракт отказал в переводе токена",
        }
        # Only exact known reasons are exposed; provider messages may contain secrets.
        reason = reasons.get(provider_message.removeprefix("execution reverted: "), "причина не распознана")
        super().__init__(
            f"Симуляция отклонена (estimateGas): {reason}. "
            "Текущая транзакция не подписана и не отправлена. Если approve уже выполнен, нужна сверка."
        )
