"""`Bars`: la serie OHLCV como objeto de valor inmutable.

Decision de diseno (ADR-0001): por el dominio circulan arrays de numpy dentro
de un objeto de valor validado, no `DataFrame` de pandas. `pyproject.toml`
declara `numerical_backend = "numpy"` y `data_backend = "pandas"`, y esa
distincion se materializa exactamente aqui: pandas entra y sale en los
adaptadores de `research/data` y en reporting; numpy es lo unico que cruza el
dominio.

Motivos:

* Un `DataFrame` arrastra un indice con semantica implicita. Dos operaciones
  que "parecen" alineadas pueden desalinearse silenciosamente por reindexado.
  En un backtest eso es un desplazamiento temporal invisible, es decir,
  look-ahead indetectable.
* Un `DataFrame` es mutable y compartido por referencia. Una funcion de
  features podria modificar la serie que otra esta usando.
* El coste por acceso a columna en pandas es alto en bucles bar a bar.

`Bars` valida sus invariantes en construccion y marca los arrays como no
escribibles. Si un objeto `Bars` existe, sus datos son correctos y estables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self, cast

import numpy as np

from app.core.determinism import array_digest, stable_hash
from app.core.exceptions import InsufficientHistory, InvariantViolation
from app.core.types import (
    ContentHash,
    FloatArray,
    IntArray,
    Symbol,
    Timeframe,
    TimestampNs,
)

#: Campos de precio, en el orden canonico usado en toda la plataforma.
OHLC_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")

#: Campos aceptados por `Bars.field`, incluidos los derivados.
PRICE_FIELDS: tuple[str, ...] = (*OHLC_FIELDS, "volume", "typical", "median")


@dataclass(frozen=True, slots=True)
class Bars:
    """Serie temporal OHLCV validada, inmutable y auto-descriptiva.

    Convencion temporal critica: `timestamp[i]` es el instante de **apertura**
    de la barra `i`. La barra `i` solo se considera conocida en su totalidad en
    `timestamp[i] + timeframe`. Toda la plataforma asume que una decision
    tomada con datos de la barra `i` se ejecuta como pronto en la apertura de
    la barra `i+1`. Ver ADR-0003.

    Attributes:
        symbol: Instrumento al que pertenece la serie.
        timeframe: Marco temporal de agregacion.
        timestamp: Aperturas en nanosegundos UTC, estrictamente crecientes.
        open, high, low, close: Precios en float64.
        volume: Volumen por barra, no negativo.
    """

    symbol: Symbol
    timeframe: Timeframe
    timestamp: IntArray
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    volume: FloatArray

    # -- construccion e invariantes ----------------------------------------

    def __post_init__(self) -> None:
        self._enforce_shapes()
        self._enforce_dtypes()
        self._freeze()
        self._enforce_invariants()

    def _enforce_shapes(self) -> None:
        lengths = {
            name: getattr(self, name).shape
            for name in ("timestamp", "open", "high", "low", "close", "volume")
        }
        unique = set(lengths.values())
        if len(unique) != 1:
            raise InvariantViolation("Los arrays de Bars tienen longitudes distintas", **lengths)
        (shape,) = unique
        if len(shape) != 1:
            raise InvariantViolation("Bars exige arrays unidimensionales", shape=shape)

    def _enforce_dtypes(self) -> None:
        if self.timestamp.dtype != np.int64:
            raise InvariantViolation(
                "timestamp debe ser int64 (nanosegundos UTC)", dtype=str(self.timestamp.dtype)
            )
        for name in ("open", "high", "low", "close", "volume"):
            array = getattr(self, name)
            if array.dtype != np.float64:
                raise InvariantViolation(
                    f"{name} debe ser float64", field=name, dtype=str(array.dtype)
                )

    def _freeze(self) -> None:
        """Impide la mutacion posterior de los buffers.

        Sin esto, `frozen=True` protegeria unicamente las referencias, no el
        contenido: `bars.close[0] = 999` seguiria funcionando.
        """
        for name in ("timestamp", "open", "high", "low", "close", "volume"):
            array: np.ndarray = getattr(self, name)
            if array.flags.owndata:
                array.setflags(write=False)
            else:
                # Es una vista de otro array; se sustituye por una copia propia
                # congelada para que congelar aqui no dependa del duenno.
                copy = array.copy()
                copy.setflags(write=False)
                object.__setattr__(self, name, copy)

    def _enforce_invariants(self) -> None:
        """Verifica las condiciones sin las cuales ningun calculo tiene sentido.

        Se comprueban solo invariantes estructurales baratas y absolutas. La
        calidad estadistica de los datos (outliers, huecos aceptables, precios
        congelados) es responsabilidad de `research.data.validators`, que
        produce un informe en lugar de lanzar.
        """
        if self.timestamp.size == 0:
            return
        if np.any(np.diff(self.timestamp) <= 0):
            bad = int(np.argmax(np.diff(self.timestamp) <= 0))
            raise InvariantViolation(
                "timestamp debe ser estrictamente creciente",
                first_bad_index=bad,
                symbol=str(self.symbol),
            )
        for name in OHLC_FIELDS:
            array = getattr(self, name)
            if not np.all(np.isfinite(array)):
                raise InvariantViolation(
                    f"{name} contiene NaN o infinitos",
                    field=name,
                    first_bad_index=int(np.argmax(~np.isfinite(array))),
                )
        if np.any(self.high < self.low):
            raise InvariantViolation(
                "high < low", first_bad_index=int(np.argmax(self.high < self.low))
            )
        upper = np.maximum(self.open, self.close)
        lower = np.minimum(self.open, self.close)
        if np.any(self.high < upper):
            raise InvariantViolation(
                "high no envuelve a open/close",
                first_bad_index=int(np.argmax(self.high < upper)),
            )
        if np.any(self.low > lower):
            raise InvariantViolation(
                "low no envuelve a open/close",
                first_bad_index=int(np.argmax(self.low > lower)),
            )
        if np.any(self.volume < 0):
            raise InvariantViolation(
                "volume negativo", first_bad_index=int(np.argmax(self.volume < 0))
            )

    @classmethod
    def from_arrays(
        cls,
        *,
        symbol: str,
        timeframe: Timeframe | str,
        timestamp: np.ndarray,
        open: np.ndarray,  # noqa: A002 - nombre del dominio OHLC
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray | None = None,
    ) -> Self:
        """Constructor tolerante que normaliza dtypes antes de validar.

        Es el unico punto donde se acepta entrada con dtype arbitrario. Los
        adaptadores de datos deben usar este constructor; el dominio usa el
        constructor directo, que exige dtypes exactos.
        """
        n = len(timestamp)
        return cls(
            symbol=Symbol(symbol),
            timeframe=Timeframe(timeframe),
            timestamp=np.asarray(timestamp, dtype=np.int64),
            open=np.asarray(open, dtype=np.float64),
            high=np.asarray(high, dtype=np.float64),
            low=np.asarray(low, dtype=np.float64),
            close=np.asarray(close, dtype=np.float64),
            volume=(
                np.zeros(n, dtype=np.float64)
                if volume is None
                else np.asarray(volume, dtype=np.float64)
            ),
        )

    # -- consulta -----------------------------------------------------------

    def __len__(self) -> int:
        return int(self.timestamp.size)

    @property
    def start(self) -> TimestampNs:
        self._require_non_empty()
        return TimestampNs(int(self.timestamp[0]))

    @property
    def end(self) -> TimestampNs:
        self._require_non_empty()
        return TimestampNs(int(self.timestamp[-1]))

    @property
    def typical_price(self) -> FloatArray:
        """(H + L + C) / 3. Base de CCI y de varios filtros de volatilidad."""
        return (self.high + self.low + self.close) / 3.0

    @property
    def median_price(self) -> FloatArray:
        """(H + L) / 2."""
        return (self.high + self.low) / 2.0

    @property
    def bar_range(self) -> FloatArray:
        """Amplitud intrabar H - L."""
        return self.high - self.low

    def field(self, name: str) -> FloatArray:
        """Acceso por nombre, necesario para bloques parametrizados por fuente.

        Permite que una configuracion declare `source = "close"` sin que el
        bloque tenga que conocer la estructura interna de `Bars`.
        """
        if name not in PRICE_FIELDS:
            raise InvariantViolation(
                f"Campo de precio desconocido: {name!r}", field=name, allowed=list(PRICE_FIELDS)
            )
        if name == "typical":
            return self.typical_price
        if name == "median":
            return self.median_price
        # `getattr` dinamico: el nombre ya se valido contra PRICE_FIELDS arriba,
        # asi que el acceso es seguro, pero mypy no puede saberlo y lo tipa como
        # Any. Sustituirlo por una cadena de `if` explicita repetiria la lista de
        # campos por segunda vez y crearia la ocasion de que las dos discrepen.
        return cast("FloatArray", getattr(self, name))

    # -- transformacion (siempre devuelve un objeto nuevo) ------------------

    def slice(self, start: int, stop: int | None = None) -> Bars:
        """Sub-serie por indice posicional, con semantica de Python.

        Se usa en walk-forward para cortar folds. Devuelve copias congeladas,
        de modo que un fold nunca puede contaminar a otro.
        """
        s = slice(start, stop)
        return Bars(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=self.timestamp[s].copy(),
            open=self.open[s].copy(),
            high=self.high[s].copy(),
            low=self.low[s].copy(),
            close=self.close[s].copy(),
            volume=self.volume[s].copy(),
        )

    def between(self, start_ns: TimestampNs | int, end_ns: TimestampNs | int) -> Bars:
        """Sub-serie por rango temporal semiabierto `[start_ns, end_ns)`."""
        left = int(np.searchsorted(self.timestamp, int(start_ns), side="left"))
        right = int(np.searchsorted(self.timestamp, int(end_ns), side="left"))
        return self.slice(left, right)

    def require(self, minimum: int, *, context: str) -> None:
        """Exige un minimo de barras antes de un calculo.

        Args:
            minimum: Numero de barras necesario.
            context: Que se iba a calcular; aparece en el mensaje de error.

        Raises:
            InsufficientHistory: si la serie es demasiado corta.
        """
        if len(self) < minimum:
            raise InsufficientHistory(
                f"{context} requiere {minimum} barras y hay {len(self)}",
                required=minimum,
                available=len(self),
                symbol=str(self.symbol),
                timeframe=str(self.timeframe),
            )

    def _require_non_empty(self) -> None:
        if self.timestamp.size == 0:
            raise InsufficientHistory("La serie esta vacia", symbol=str(self.symbol))

    # -- identidad ----------------------------------------------------------

    def digest(self) -> ContentHash:
        """Huella de contenido de la serie completa.

        Es la pieza que hace auditable un backtest: el artefacto guarda este
        digest y cualquiera puede comprobar que se ejecuto exactamente sobre
        estos datos y no sobre una version reprocesada.
        """
        return stable_hash(
            {
                "symbol": str(self.symbol),
                "timeframe": str(self.timeframe),
                "n": len(self),
                "timestamp": array_digest(self.timestamp),
                "open": array_digest(self.open),
                "high": array_digest(self.high),
                "low": array_digest(self.low),
                "close": array_digest(self.close),
                "volume": array_digest(self.volume),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Metadatos (no los datos) para logs y artefactos."""
        return {
            "symbol": str(self.symbol),
            "timeframe": str(self.timeframe),
            "bars": len(self),
            "start_ns": int(self.timestamp[0]) if len(self) else None,
            "end_ns": int(self.timestamp[-1]) if len(self) else None,
            "digest": str(self.digest()),
        }

    def __repr__(self) -> str:
        if not len(self):
            return f"Bars({self.symbol} {self.timeframe} empty)"
        return (
            f"Bars({self.symbol} {self.timeframe} n={len(self)} "
            f"[{self.timestamp[0]}..{self.timestamp[-1]}])"
        )


__all__ = ["OHLC_FIELDS", "PRICE_FIELDS", "Bars"]
