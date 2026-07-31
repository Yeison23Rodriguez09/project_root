"""El contrato de datos: que garantiza `Bars` a quien lo recibe.

Es la otra mitad del contrato de dominio, junto a `test_domain_result_contract`.
Su valor esta en lo que AHORRA: si `Bars` garantiza integridad estructural,
ningun consumidor -backtest, features, walk-forward, discovery- necesita repetir
las comprobaciones, y ninguno puede olvidarlas. "Si existe un `Bars`, sus datos
son correctos" solo vale si el tipo lo hace cumplir.

La frontera entre lo que se comprueba aqui y lo que no es deliberada y esta
declarada en el propio `Bars`: las invariantes ESTRUCTURALES lanzan, y la calidad
estadistica -huecos, outliers, precios congelados- la INFORMA
`research.data.validators`. Un hueco de fin de semana no es un fichero corrupto;
un timestamp fuera de rejilla si.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.exceptions import InvariantViolation
from app.core.types import Timeframe
from app.domain.entities.bars import Bars

M15_NS = Timeframe.M15.nanoseconds


def _series(count: int = 8, *, start: int = 0, step: int | None = None) -> dict[str, object]:
    """Serie M15 valida sobre la que aplicar una sola perturbacion por test."""
    step = M15_NS if step is None else step
    timestamp = np.arange(start, start + count * step, step, dtype=np.int64)
    close = np.linspace(1.1000, 1.1000 + 0.0001 * count, count)
    return {
        "symbol": "EURUSD",
        "timeframe": Timeframe.M15,
        "timestamp": timestamp,
        "open": close - 0.00005,
        "high": close + 0.00020,
        "low": close - 0.00020,
        "close": close,
        "volume": np.full(count, 100.0),
    }


# ---------------------------------------------------------------------------
# Lo que ya garantizaba
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_valid_series_is_accepted() -> None:
    bars = Bars.from_arrays(**_series())  # type: ignore[arg-type]

    assert len(bars) == 8
    assert str(bars.symbol) == "EURUSD"
    assert bars.timeframe is Timeframe.M15


@pytest.mark.unit
def test_timestamps_must_strictly_increase() -> None:
    """Cubre tambien los duplicados: un timestamp repetido no es creciente."""
    data = _series()
    ts = np.array(data["timestamp"], dtype=np.int64)

    ts[3] = ts[2]  # duplicado
    with pytest.raises(InvariantViolation):
        Bars.from_arrays(**{**data, "timestamp": ts})  # type: ignore[arg-type]

    ts[3] = ts[2] - M15_NS  # retroceso
    with pytest.raises(InvariantViolation):
        Bars.from_arrays(**{**data, "timestamp": ts})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("campo", "delta"),
    [("high", -0.01), ("low", 0.01)],
    ids=["high_no_envuelve", "low_no_envuelve"],
)
def test_ohlc_must_be_consistent(campo: str, delta: float) -> None:
    """`low <= open/close <= high`, sin lo cual una vela no es una vela."""
    data = _series()
    array = np.array(data[campo], dtype=np.float64)
    array[4] += delta

    with pytest.raises(InvariantViolation):
        Bars.from_arrays(**{**data, campo: array})  # type: ignore[arg-type]


@pytest.mark.unit
def test_prices_reject_nan_and_infinity() -> None:
    data = _series()
    for valor in (np.nan, np.inf):
        close = np.array(data["close"], dtype=np.float64)
        close[2] = valor
        with pytest.raises(InvariantViolation):
            Bars.from_arrays(**{**data, "close": close})  # type: ignore[arg-type]


@pytest.mark.unit
def test_volume_cannot_be_negative() -> None:
    data = _series()
    volume = np.array(data["volume"], dtype=np.float64)
    volume[1] = -1.0

    with pytest.raises(InvariantViolation):
        Bars.from_arrays(**{**data, "volume": volume})  # type: ignore[arg-type]


@pytest.mark.unit
def test_symbol_and_timeframe_are_part_of_the_dataset() -> None:
    """No son metadatos opcionales que el consumidor deba arrastrar aparte.

    Que viajen DENTRO del objeto es lo que permite que un motor reciba `Bars` y
    sepa sobre que instrumento y que rejilla esta calculando, sin que nadie
    tenga que pasarlos en paralelo y sin que puedan desincronizarse.
    """
    campos = set(Bars.__dataclass_fields__)

    assert {"symbol", "timeframe"} <= campos
    for obligatorio in ("symbol", "timeframe"):
        incompleto = {k: v for k, v in _series().items() if k != obligatorio}
        with pytest.raises(TypeError):
            Bars.from_arrays(**incompleto)  # type: ignore[arg-type]


@pytest.mark.unit
def test_buffers_are_not_writable() -> None:
    """`frozen=True` solo protege las referencias; el contenido hay que congelarlo.

    Sin esto, `bars.close[0] = 999` funcionaria y la garantia "si existe, es
    correcto" duraria hasta el primer consumidor descuidado.
    """
    bars = Bars.from_arrays(**_series())  # type: ignore[arg-type]

    for campo in ("timestamp", "open", "high", "low", "close", "volume"):
        with pytest.raises(ValueError):
            getattr(bars, campo)[0] = 0


@pytest.mark.unit
def test_construction_does_not_touch_the_callers_memory() -> None:
    """Construir un `Bars` no puede dejar de solo lectura los arrays de quien llama.

    Ocurria: `np.asarray` devuelve el MISMO objeto cuando el dtype ya coincide, y
    el congelado se aplicaba en sitio a todo array con `owndata`. El adaptador
    que leia un historico, construia `Bars` y reutilizaba su buffer se estrellaba
    con "assignment destination is read-only" desde una linea sin relacion.

    Peor que el efecto lateral era su imprevisibilidad: `owndata` depende de como
    se creo el array. `np.arange` y `np.full` lo traen a True y quedaban
    congelados; `np.linspace` a False y se copiaba. Dos adaptadores identicos
    salvo en eso se comportaban distinto.
    """
    step = M15_NS
    propios = {
        "timestamp": np.arange(0, 6 * step, step, dtype=np.int64),  # owndata=True
        "volume": np.full(6, 100.0),                                # owndata=True
    }
    close = np.asarray([1.10, 1.11, 1.12, 1.13, 1.14, 1.15], dtype=np.float64)

    bars = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=propios["timestamp"],
        open=close - 0.0001,
        high=close + 0.0002,
        low=close - 0.0002,
        close=close,
        volume=propios["volume"],
    )

    for nombre, array in propios.items():
        assert array.flags.writeable, f"construir Bars congelo el array {nombre} del llamante"
        array[0] = 1  # el llamante conserva su memoria
    assert close.flags.writeable

    # Y `Bars` sigue siendo inmutable: no comparte buffer con nadie.
    assert bars.timestamp is not propios["timestamp"]
    assert bars.close is not close
    with pytest.raises(ValueError):
        bars.close[0] = 999.0


@pytest.mark.unit
def test_timestamps_are_timezone_free_by_construction() -> None:
    """Nanosegundos desde epoca en `int64`: no hay zona horaria que normalizar.

    La normalizacion a UTC no se comprueba porque no puede fallar aqui: un
    entero de nanosegundos no lleva zona. El riesgo real -un adaptador que
    derive el instante de una hora local- se ataja en el adaptador, no en el
    dominio, y por eso el dtype es parte del contrato y no una preferencia.
    """
    data = _series()
    bars = Bars.from_arrays(**data)  # type: ignore[arg-type]
    assert bars.timestamp.dtype == np.int64

    with pytest.raises(InvariantViolation):
        Bars(
            symbol=bars.symbol,
            timeframe=bars.timeframe,
            timestamp=np.asarray(data["timestamp"], dtype=np.float64),  # type: ignore[arg-type]
            open=bars.open,
            high=bars.high,
            low=bars.low,
            close=bars.close,
            volume=bars.volume,
        )


# ---------------------------------------------------------------------------
# Lo que se anade: la serie vive sobre la rejilla que declara
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_bar_off_the_timeframe_grid_is_rejected() -> None:
    """Sin esto, `timeframe` seria una etiqueta y no un hecho.

    Todo lo que se deriva de el -anualizacion, warmup, alineacion de folds- se
    calcularia sobre un supuesto que nadie comprueba.
    """
    data = _series()
    ts = np.array(data["timestamp"], dtype=np.int64)
    ts[5:] += M15_NS // 3  # desplaza media rejilla en adelante

    with pytest.raises(InvariantViolation):
        Bars.from_arrays(**{**data, "timestamp": ts})  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_series_declaring_the_wrong_timeframe_is_rejected() -> None:
    """Una serie H1 que dice ser M15 tiene saltos de cuatro periodos... y pasa.

    Ese caso NO lo atrapa esta invariante y es correcto que no lo haga: cuatro
    periodos es un multiplo legitimo, indistinguible de un hueco de mercado. Lo
    que si se atrapa es el caso inverso, que es el destructivo: una serie M15
    que dice ser H1, porque entonces los saltos son fracciones de la rejilla
    declarada.
    """
    data = _series(step=M15_NS)
    with pytest.raises(InvariantViolation):
        Bars.from_arrays(**{**data, "timeframe": Timeframe.H1})  # type: ignore[arg-type]


@pytest.mark.unit
def test_market_gaps_are_accepted_because_they_are_multiples() -> None:
    """El caso que decide el diseno de esta invariante.

    EURUSD en M15 tiene un hueco de unas 48 horas cada fin de semana. Exigir
    espaciado CONSTANTE en lugar de multiplo rechazaria toda serie de mercado
    real, y el contrato seria inservible con datos de verdad.

    Cuantos huecos hay, cuando y de que tamano lo INFORMA el validador de
    calidad: es una senal, no una corrupcion.
    """
    data = _series(count=6)
    ts = np.array(data["timestamp"], dtype=np.int64)
    ts[3:] += 48 * 60 * 60 * 1_000_000_000  # fin de semana

    bars = Bars.from_arrays(**{**data, "timestamp": ts})  # type: ignore[arg-type]

    assert len(bars) == 6


@pytest.mark.unit
def test_broker_sessions_off_the_absolute_grid_are_accepted() -> None:
    """No se exige `timestamp % timeframe == 0`, y es deliberado.

    Las barras diarias de la mayoria de brokers cierran en su medianoche de
    servidor, no en la de UTC. Exigir alineacion absoluta rechazaria una D1
    legitima de MT5. La alineacion es convencion del proveedor; la periodicidad,
    hecho de la serie.
    """
    offset = 17 * 60 * 60 * 1_000_000_000  # cierre a las 17:00
    data = _series(count=5, start=offset, step=Timeframe.D1.nanoseconds)

    bars = Bars.from_arrays(**{**data, "timeframe": Timeframe.D1})  # type: ignore[arg-type]

    assert len(bars) == 5
    assert int(bars.timestamp[0]) % Timeframe.D1.nanoseconds != 0


@pytest.mark.unit
@pytest.mark.parametrize("count", [0, 1])
def test_degenerate_series_do_not_trip_the_grid_check(count: int) -> None:
    """Con menos de dos barras no hay salto que comprobar."""
    bars = Bars.from_arrays(**_series(count=count))  # type: ignore[arg-type]

    assert len(bars) == count
