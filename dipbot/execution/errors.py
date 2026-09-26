"""Execution outcomes that must retain the transaction latch."""


class UncertainTransaction(RuntimeError):
    pass
