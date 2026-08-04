"""Particionado de una serie en folds de walk-forward.

Dos esquemas, y la diferencia entre ellos no es cosmetica:

    ROLLING   la ventana de ajuste se desplaza y mantiene su tamano. Supone que
              el mercado cambia y que lo aprendido hace cinco anos ya no sirve.
    ANCHORED  la ventana de ajuste empieza siempre al principio y crece. Supone
              que mas historia es siempre mejor.

Ninguno es correcto a priori: son hipotesis distintas sobre la estacionariedad
del mercado, y comparar sus resultados es informacion en si misma. Por eso se
implementan los dos y no se elige uno por defecto.

Se particiona por NUMERO DE BARRAS y no por duracion. Un fold de "tres meses"
contiene muy pocas barras si cae en agosto y muchas si cae en octubre, y
entonces los folds no son comparables entre si. Lo que importa para la
estadistica es cuantas observaciones hay, no cuanto tiempo abarcan.
"""

from __future__ import annotations

from app.core.exceptions import InsufficientHistory, InvariantViolation
from app.core.types import TimestampNs
from app.domain.entities.bars import Bars
from app.domain.value_objects.time_range import Fold, TimeRange


def _range_of(bars: Bars, start: int, stop: int) -> TimeRange:
    """`TimeRange` que cubre las barras `[start, stop)` por indice.

    El final es la apertura de la barra `stop`, o el CIERRE de la ultima si
    `stop` cae fuera. `timestamp[i]` es la apertura, asi que usar la ultima
    apertura como final dejaria fuera la ultima barra del fold.
    """
    first = TimestampNs(int(bars.timestamp[start]))
    if stop < len(bars):
        return TimeRange(start_ns=first, end_ns=TimestampNs(int(bars.timestamp[stop])))
    last_close = int(bars.timestamp[-1]) + bars.timeframe.nanoseconds
    return TimeRange(start_ns=first, end_ns=TimestampNs(last_close))


def _validate(bars: Bars, *, folds: int, oos_bars: int, purge_bars: int) -> None:
    if folds < 1:
        raise InvariantViolation("Se necesita al menos un fold", folds=folds)
    if oos_bars < 1:
        raise InvariantViolation("El out-of-sample no puede estar vacio", oos_bars=oos_bars)
    if purge_bars < 0:
        raise InvariantViolation("purge_bars no puede ser negativo", purge_bars=purge_bars)
    if len(bars) < 2:
        raise InsufficientHistory(
            "Particionar exige una serie con barras", required=2, available=len(bars)
        )


def rolling(
    bars: Bars,
    *,
    folds: int,
    is_bars: int,
    oos_bars: int,
    purge_bars: int = 0,
) -> tuple[Fold, ...]:
    """Ventana de ajuste de tamano fijo que avanza `oos_bars` en cada fold.

    Raises:
        InvariantViolation: parametros incoherentes.
        InsufficientHistory: la serie no da para los folds pedidos.
    """
    _validate(bars, folds=folds, oos_bars=oos_bars, purge_bars=purge_bars)
    if is_bars < 1:
        raise InvariantViolation("El in-sample no puede estar vacio", is_bars=is_bars)

    needed = is_bars + purge_bars + oos_bars + (folds - 1) * oos_bars
    if len(bars) < needed:
        raise InsufficientHistory(
            "La serie no alcanza para los folds pedidos",
            required=needed,
            available=len(bars),
            folds=folds,
        )

    built: list[Fold] = []
    for index in range(folds):
        is_start = index * oos_bars
        is_stop = is_start + is_bars
        oos_start = is_stop + purge_bars
        oos_stop = oos_start + oos_bars
        built.append(_fold(bars, index, is_start, is_stop, oos_start, oos_stop))
    return tuple(built)


def anchored(
    bars: Bars,
    *,
    folds: int,
    oos_bars: int,
    min_is_bars: int,
    purge_bars: int = 0,
) -> tuple[Fold, ...]:
    """Ventana de ajuste anclada al inicio, que crece en cada fold."""
    _validate(bars, folds=folds, oos_bars=oos_bars, purge_bars=purge_bars)
    if min_is_bars < 1:
        raise InvariantViolation("El in-sample inicial no puede estar vacio")

    needed = min_is_bars + purge_bars + oos_bars + (folds - 1) * oos_bars
    if len(bars) < needed:
        raise InsufficientHistory(
            "La serie no alcanza para los folds pedidos",
            required=needed,
            available=len(bars),
            folds=folds,
        )

    built: list[Fold] = []
    for index in range(folds):
        is_stop = min_is_bars + index * oos_bars
        oos_start = is_stop + purge_bars
        oos_stop = oos_start + oos_bars
        built.append(_fold(bars, index, 0, is_stop, oos_start, oos_stop))
    return tuple(built)


def _fold(
    bars: Bars, index: int, is_start: int, is_stop: int, oos_start: int, oos_stop: int
) -> Fold:
    in_sample = _range_of(bars, is_start, is_stop)
    out_of_sample = _range_of(bars, oos_start, oos_stop)
    return Fold(
        index=index,
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        purge_ns=int(out_of_sample.start_ns) - int(in_sample.end_ns),
    )


__all__ = ["anchored", "rolling"]
