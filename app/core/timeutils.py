"""Aritmetica temporal sobre nanosegundos UTC.

Todo el dominio trabaja con `int64` de nanosegundos desde epoch. Este modulo
concentra las unicas conversiones permitidas hacia y desde representaciones
humanas, y ofrece derivaciones de calendario (hora, dia de semana, sesion) sin
depender de pandas ni de la zona horaria del sistema operativo.

Nota sobre correccion: la epoca UNIX (1970-01-01) fue un jueves. De ahi el
desplazamiento `+3` para obtener el dia de la semana con lunes = 0.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from app.core.types import (
    NS_PER_DAY,
    NS_PER_HOUR,
    NS_PER_MINUTE,
    NS_PER_SECOND,
    BoolArray,
    IntArray,
    Timeframe,
    TimestampNs,
)

# ---------------------------------------------------------------------------
# Conversiones escalares (frontera humano <-> dominio)
# ---------------------------------------------------------------------------


def to_ns(value: datetime | str | int | np.datetime64) -> TimestampNs:
    """Convierte una representacion temporal cualquiera a nanosegundos UTC.

    Un `datetime` naive se interpreta como UTC de forma explicita. No se asume
    la zona local del proceso: eso haria que el mismo backtest produjera
    resultados distintos en dos maquinas.
    """
    if isinstance(value, bool):  # bool es subclase de int; atajarlo es intencional
        raise TypeError("Un booleano no es un timestamp valido")
    if isinstance(value, int):
        return TimestampNs(value)
    if isinstance(value, np.datetime64):
        return TimestampNs(int(value.astype("datetime64[ns]").astype(np.int64)))
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return TimestampNs(int(aware.timestamp() * NS_PER_SECOND))
    if isinstance(value, str):
        return TimestampNs(int(np.datetime64(value, "ns").astype(np.int64)))
    raise TypeError(f"Tipo temporal no soportado: {type(value).__name__}")


def from_ns(ts: TimestampNs | int) -> datetime:
    """Convierte nanosegundos UTC a `datetime` con zona explicita."""
    return datetime.fromtimestamp(int(ts) / NS_PER_SECOND, tz=UTC)


def format_ns(ts: TimestampNs | int) -> str:
    """Representacion ISO-8601 estable, usada en logs y nombres de artefacto."""
    return from_ns(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Derivaciones de calendario vectorizadas
# ---------------------------------------------------------------------------


def hour_of_day(ts_ns: IntArray) -> IntArray:
    """Hora UTC [0, 23] de cada timestamp."""
    return ((ts_ns // NS_PER_HOUR) % 24).astype(np.int64)


def minute_of_day(ts_ns: IntArray) -> IntArray:
    """Minuto UTC [0, 1439] de cada timestamp."""
    return ((ts_ns // NS_PER_MINUTE) % 1440).astype(np.int64)


def day_of_week(ts_ns: IntArray) -> IntArray:
    """Dia de la semana con lunes = 0 y domingo = 6."""
    days = ts_ns // NS_PER_DAY
    return ((days + 3) % 7).astype(np.int64)


def day_index(ts_ns: IntArray) -> IntArray:
    """Numero de dia absoluto desde epoch.

    Sirve para agrupar barras por jornada sin construir fechas, por ejemplo
    para contar operaciones diarias o detectar el primer bar del dia.
    """
    return (ts_ns // NS_PER_DAY).astype(np.int64)


def is_new_day(ts_ns: IntArray) -> BoolArray:
    """Marca la primera barra de cada jornada UTC.

    La primera barra de la serie se marca como inicio de dia por definicion.
    """
    days = day_index(ts_ns)
    flags = np.empty(days.shape, dtype=np.bool_)
    if days.size == 0:
        return flags
    flags[0] = True
    flags[1:] = days[1:] != days[:-1]
    return flags


def in_hour_window(ts_ns: IntArray, start_hour: int, end_hour: int) -> BoolArray:
    """Mascara de pertenencia a una ventana horaria UTC semiabierta.

    La ventana es `[start_hour, end_hour)`. Si `start_hour > end_hour` la
    ventana cruza medianoche y se interpreta como union de los dos tramos, que
    es el comportamiento esperado para la sesion asiatica.

    Una ventana con `start_hour == end_hour` se interpreta como **vacia**, no
    como "todo el dia". Es la lectura conservadora: ante una configuracion
    ambigua el sistema no opera, en lugar de operar 24 horas por accidente.

    Raises:
        ValueError: si alguna hora esta fuera de [0, 24].
    """
    if not (0 <= start_hour <= 24 and 0 <= end_hour <= 24):
        raise ValueError(f"Horas fuera de rango: start={start_hour} end={end_hour}")
    hours = hour_of_day(ts_ns)
    if start_hour == end_hour:
        return np.zeros(hours.shape, dtype=np.bool_)
    if start_hour < end_hour:
        return (hours >= start_hour) & (hours < end_hour)
    return (hours >= start_hour) | (hours < end_hour)


# ---------------------------------------------------------------------------
# Rejilla temporal
# ---------------------------------------------------------------------------


def expected_step_ns(timeframe: Timeframe) -> int:
    """Separacion nominal entre barras consecutivas."""
    return timeframe.nanoseconds


def is_aligned(ts_ns: IntArray, timeframe: Timeframe) -> BoolArray:
    """Marca los timestamps alineados con la rejilla del timeframe.

    Una barra M15 debe abrir en :00, :15, :30 o :45. Un timestamp desalineado
    revela un problema de origen de datos (agregacion mal hecha, huso horario
    del broker aplicado sin normalizar) y debe detectarse antes de calcular
    nada sobre esa serie.
    """
    aligned: BoolArray = (ts_ns % timeframe.nanoseconds) == 0
    return aligned


def bar_gaps(ts_ns: IntArray, timeframe: Timeframe) -> IntArray:
    """Numero de barras ausentes antes de cada posicion.

    Devuelve un array de la misma longitud; la posicion 0 vale siempre 0 porque
    no hay barra anterior con la que comparar. Un valor `k > 0` indica que
    faltan `k` barras entre `i-1` e `i`.

    Los huecos no son necesariamente un error: fines de semana y festivos son
    huecos legitimos. Interpretarlos es responsabilidad del validador de datos,
    que conoce el calendario del instrumento; aqui solo se cuentan.
    """
    gaps = np.zeros(ts_ns.shape, dtype=np.int64)
    if ts_ns.size < 2:
        return gaps
    step = timeframe.nanoseconds
    deltas = np.diff(ts_ns)
    gaps[1:] = np.maximum(deltas // step - 1, 0)
    return gaps


__all__ = [
    "bar_gaps",
    "day_index",
    "day_of_week",
    "expected_step_ns",
    "format_ns",
    "from_ns",
    "hour_of_day",
    "in_hour_window",
    "is_aligned",
    "is_new_day",
    "minute_of_day",
    "to_ns",
]
