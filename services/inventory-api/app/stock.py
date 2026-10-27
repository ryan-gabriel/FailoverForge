from __future__ import annotations

import asyncio

DEFAULT_STOCK: dict[str, int] = {
    "SKU-1001": 100,
    "SKU-1002": 80,
    "SKU-1003": 60,
    "SKU-1004": 50,
    "SKU-1005": 40,
    "SKU-1006": 30,
}


class UnknownSku(Exception):
    pass


class InsufficientStock(Exception):
    def __init__(self, sku: str) -> None:
        self.sku = sku
        super().__init__(sku)


class Stock:
    """In-memory SKU map, seeded at startup (PRD §5)."""

    def __init__(self, initial: dict[str, int] | None = None) -> None:
        self._lock = asyncio.Lock()
        self._items = dict(initial if initial is not None else DEFAULT_STOCK)

    def get(self, sku: str) -> int | None:
        return self._items.get(sku)

    async def reserve(self, sku: str, qty: int) -> int:
        async with self._lock:
            available = self._items.get(sku)
            if available is None:
                raise UnknownSku(sku)
            if available < qty:
                raise InsufficientStock(sku)
            self._items[sku] = available - qty
            return available - qty

    async def reset(self, initial: dict[str, int] | None = None) -> dict[str, int]:
        async with self._lock:
            self._items = dict(initial if initial is not None else DEFAULT_STOCK)
            return dict(self._items)

    def as_dict(self) -> dict[str, int]:
        return dict(self._items)
