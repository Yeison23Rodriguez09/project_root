"""Doble del modulo `MetaTrader5`, configurable por test.

Existe porque `configs/runtime.toml` exige `require_synthetic_data_only` en modo
`ci`: el pipeline no puede hablar nunca con un broker real. Un test que dependa
de un terminal es un test que solo pasa en la maquina de quien lo escribio, y
falla en CI por el motivo equivocado.

Reproduce la superficie que consume `MT5MarketDataAdapter` y solo esa. No imita
el modulo entero: un doble que finge mas de lo que se usa acaba divergiendo del
original sin que nadie lo note.

Los rates se devuelven como array ESTRUCTURADO de numpy, que es la forma exacta
en que `copy_rates_*` los publica: si el doble devolviera un diccionario o una
lista, el adaptador pasaria los tests y fallaria contra el terminal real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: Tipo del array que publica `copy_rates_*`. Los nombres y el orden son los de
#: MetaTrader 5; `spread` y `real_volume` se incluyen aunque el adaptador no los
#: use, porque estan en el original y su ausencia ocultaria un acceso indebido.
RATES_DTYPE = np.dtype(
    [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<u8"),
        ("spread", "<i4"),
        ("real_volume", "<u8"),
    ]
)

#: Constantes de marco temporal, con los valores reales de MetaTrader 5.
TIMEFRAMES: dict[str, int] = {
    "TIMEFRAME_M1": 1,
    "TIMEFRAME_M5": 5,
    "TIMEFRAME_M15": 15,
    "TIMEFRAME_M30": 30,
    "TIMEFRAME_H1": 16385,
    "TIMEFRAME_H4": 16388,
    "TIMEFRAME_D1": 16408,
}


@dataclass(frozen=True)
class FakeTerminalInfo:
    connected: bool = True
    trade_allowed: bool = True
    path: str = r"C:\Program Files\FakeTerminal"


@dataclass(frozen=True)
class FakeAccountInfo:
    login: int = 999_000_111
    server: str = "FakeBroker-Demo"


@dataclass(frozen=True)
class FakeSymbolInfo:
    name: str


def rates_from_seconds(
    start_s: int,
    step_s: int,
    count: int,
    *,
    base: float = 1.1000,
) -> np.ndarray:
    """Serie sintetica coherente, en el formato que devuelve MT5."""
    rows = []
    for i in range(count):
        close = base + 0.0001 * i
        rows.append(
            (
                start_s + i * step_s,
                close - 0.00005,
                close + 0.00020,
                close - 0.00020,
                close,
                100 + i,
                12,
                0,
            )
        )
    return np.array(rows, dtype=RATES_DTYPE)


@dataclass
class FakeMT5:
    """Terminal simulado. Cada atributo gobierna una respuesta.

    Registra ademas las llamadas recibidas, para poder afirmar que el adaptador
    hace lo que dice: que selecciona el simbolo antes de pedir datos, que cierra
    la conexion, y que pide el rango correcto.
    """

    initialize_ok: bool = True
    terminal_info_value: FakeTerminalInfo | None = field(default_factory=FakeTerminalInfo)
    account_info_value: FakeAccountInfo | None = field(default_factory=FakeAccountInfo)
    symbols: tuple[str, ...] = ("XAUUSD", "EURUSD", "GBPUSD")
    symbol_select_ok: bool = True
    rates: np.ndarray | None = None
    error: tuple[int, str] = (0, "Success")

    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    initialized: bool = False

    # -- constantes del modulo ----------------------------------------------

    def __getattr__(self, name: str) -> int:
        """Expone `TIMEFRAME_*` como lo hace el modulo real."""
        if name in TIMEFRAMES:
            return TIMEFRAMES[name]
        raise AttributeError(name)

    # -- superficie que consume el adaptador --------------------------------

    def initialize(self, *args: Any, **kwargs: Any) -> bool:
        self.calls.append(("initialize", args))
        self.initialized = self.initialize_ok
        return self.initialize_ok

    def shutdown(self) -> None:
        self.calls.append(("shutdown", ()))
        self.initialized = False

    def terminal_info(self) -> FakeTerminalInfo | None:
        return self.terminal_info_value

    def account_info(self) -> FakeAccountInfo | None:
        return self.account_info_value

    def symbols_total(self) -> int:
        return len(self.symbols)

    def symbols_get(self) -> tuple[FakeSymbolInfo, ...] | None:
        self.calls.append(("symbols_get", ()))
        return tuple(FakeSymbolInfo(name) for name in self.symbols)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.calls.append(("symbol_select", (symbol, enable)))
        return self.symbol_select_ok

    def copy_rates_range(self, symbol: str, timeframe: int, start: Any, end: Any) -> Any:
        self.calls.append(("copy_rates_range", (symbol, timeframe, start, end)))
        return self.rates

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any:
        self.calls.append(("copy_rates_from_pos", (symbol, timeframe, start, count)))
        return self.rates

    def last_error(self) -> tuple[int, str]:
        return self.error

    # -- utilidades de asercion ---------------------------------------------

    def called(self, name: str) -> bool:
        return any(call == name for call, _ in self.calls)

    def arguments_of(self, name: str) -> tuple[Any, ...]:
        for call, args in self.calls:
            if call == name:
                return args
        raise AssertionError(f"El adaptador nunca llamo a {name!r}. Llamadas: {self.calls}")


__all__ = [
    "RATES_DTYPE",
    "TIMEFRAMES",
    "FakeAccountInfo",
    "FakeMT5",
    "FakeSymbolInfo",
    "FakeTerminalInfo",
    "rates_from_seconds",
]
