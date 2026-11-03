from abc import ABC, abstractmethod


class BrokerAdapter(ABC):
    """Common broker contract. Implementations must be separately validated.

    Samma gränssnitt ska senare användas för en riktig mäklare (t.ex.
    Interactive Brokers), så att resten av programmet inte behöver ändras.
    """

    @abstractmethod
    def get_account(self): ...

    @abstractmethod
    def get_positions(self): ...

    @abstractmethod
    def submit_order(self, symbol, side, quantity, order_type="market", price=None, **info): ...

    @abstractmethod
    def cancel_order(self, order_id): ...
