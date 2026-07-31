"""El adaptador de historicos: lee, parsea, construye `Bars` y entrega.

Lo que mas importa aqui son los tests de lo que el adaptador NO hace. Un
adaptador que repara datos es peor que uno que falla: convierte un fichero
corrupto en uno que parece sano, y el error reaparece mucho despues como un
resultado inexplicable en lugar de como una excepcion con nombre y ruta.

Por eso cada supuesto de reparacion tentadora -ordenar, rellenar, interpolar-
tiene su test comprobando que el adaptador se niega.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.core.exceptions import DataIntegrityError, DataSourceError, InvariantViolation
from app.core.types import Symbol, Timeframe, TimestampNs
from app.research.data.layout import DatasetLayout
from app.research.data.parquet_source import ParquetMarketData
from app.shared.ports import MarketDataPort

pytest.importorskip("pyarrow", reason="El adaptador de Parquet requiere pyarrow")

M15_NS = Timeframe.M15.nanoseconds


def _write(
    root: Path,
    symbol: str = "EURUSD",
    timeframe: Timeframe = Timeframe.M15,
    *,
    count: int = 10,
    timestamp: np.ndarray | None = None,
    columns: dict[str, np.ndarray] | None = None,
    drop: tuple[str, ...] = (),
) -> Path:
    """Escribe un historico Parquet valido, con las perturbaciones que se pidan."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    ts = (
        np.arange(0, count * M15_NS, M15_NS, dtype=np.int64)
        if timestamp is None
        else timestamp
    )
    close = np.linspace(1.1000, 1.1000 + 0.0001 * len(ts), len(ts))
    data: dict[str, np.ndarray] = {
        "timestamp": ts,
        "open": close - 0.00005,
        "high": close + 0.00020,
        "low": close - 0.00020,
        "close": close,
        "volume": np.full(len(ts), 100.0),
    }
    if columns:
        data.update(columns)
    for name in drop:
        data.pop(name, None)

    target = root / symbol / f"{timeframe}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(data), target)
    return target


# ---------------------------------------------------------------------------
# Cumple el puerto y entrega una serie
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_adapter_satisfies_the_port(tmp_path: Path) -> None:
    """Cumplimiento ESTRUCTURAL: no hereda del Protocol, encaja con el.

    Es lo que permite que el adaptador MT5 de la rebanada siguiente sustituya a
    este sin que ningun consumidor cambie una linea.
    """
    assert isinstance(ParquetMarketData(DatasetLayout(root=tmp_path)), MarketDataPort)


@pytest.mark.integration
def test_a_valid_history_becomes_validated_bars(tmp_path: Path) -> None:
    _write(tmp_path, count=10)

    bars = ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.M15)

    assert len(bars) == 10
    assert str(bars.symbol) == "EURUSD"
    assert bars.timeframe is Timeframe.M15
    # Entrega el contrato, no una lista de velas: los buffers vienen congelados.
    with pytest.raises(ValueError):
        bars.close[0] = 0.0


@pytest.mark.integration
def test_volume_is_optional(tmp_path: Path) -> None:
    """Hay proveedores que no lo publican, y un volumen inventado es peor.

    `Bars` admite su ausencia con ceros; lo que no se hace es rellenarlo con una
    estimacion, que seria un dato fabricado indistinguible de uno medido.
    """
    _write(tmp_path, count=6, drop=("volume",))

    bars = ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.M15)

    assert len(bars) == 6
    assert np.all(np.asarray(bars.volume) == 0.0)


# ---------------------------------------------------------------------------
# Seleccion de rango: selecciona, no repara
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_range_selection_includes_both_ends(tmp_path: Path) -> None:
    """Cerrado por los dos lados porque `timestamp[i]` es la APERTURA.

    Pedir hasta `end_ns` y excluirla dejaria fuera la barra que abre exactamente
    en ese instante, que es la que el llamante espera al pedir un rango que
    termina ahi.
    """
    _write(tmp_path, count=10)

    bars = ParquetMarketData(DatasetLayout(root=tmp_path)).load(
        Symbol("EURUSD"),
        Timeframe.M15,
        start_ns=TimestampNs(2 * M15_NS),
        end_ns=TimestampNs(5 * M15_NS),
    )

    assert len(bars) == 4
    assert int(bars.timestamp[0]) == 2 * M15_NS
    assert int(bars.timestamp[-1]) == 5 * M15_NS


@pytest.mark.integration
def test_an_empty_range_is_a_fact_not_an_error(tmp_path: Path) -> None:
    """Cero barras es representable y distinto de un fallo.

    Un rango sin datos es informacion legitima -ese instrumento no cotizo ahi- y
    convertirlo en excepcion obligaria a cada consumidor a distinguir por el
    mensaje entre "no hay" y "esta roto".
    """
    _write(tmp_path, count=5)

    bars = ParquetMarketData(DatasetLayout(root=tmp_path)).load(
        Symbol("EURUSD"),
        Timeframe.M15,
        start_ns=TimestampNs(10_000 * M15_NS),
    )

    assert len(bars) == 0


# ---------------------------------------------------------------------------
# Lo que el adaptador NO hace
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_unsorted_rows_are_rejected_not_sorted(tmp_path: Path) -> None:
    """La tentacion mas fuerte, y la que mas dano hace.

    Ordenar en silencio convierte una fuente rota en una que parece sana. Si el
    proveedor entrega filas desordenadas hay un fallo aguas arriba que hay que
    ver, no absorber.
    """
    ts = np.arange(0, 8 * M15_NS, M15_NS, dtype=np.int64)
    ts[[3, 4]] = ts[[4, 3]]
    _write(tmp_path, timestamp=ts)

    with pytest.raises(InvariantViolation):
        ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.M15)


@pytest.mark.integration
def test_off_grid_timestamps_are_rejected_not_corrected(tmp_path: Path) -> None:
    """Un desplazamiento de media rejilla no se redondea: se rechaza."""
    ts = np.arange(0, 8 * M15_NS, M15_NS, dtype=np.int64)
    ts[4:] += M15_NS // 3
    _write(tmp_path, timestamp=ts)

    with pytest.raises(InvariantViolation):
        ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.M15)


@pytest.mark.integration
def test_market_gaps_pass_through_untouched(tmp_path: Path) -> None:
    """El hueco NO se rellena, y tampoco es un error.

    Es la contrapartida del test anterior: el adaptador no repara, pero tampoco
    inventa un problema donde hay un fin de semana. Cuantos huecos hay y de que
    tamano lo informa el validador de calidad, no este fichero.
    """
    ts = np.arange(0, 6 * M15_NS, M15_NS, dtype=np.int64)
    ts[3:] += 48 * 60 * 60 * 1_000_000_000
    _write(tmp_path, timestamp=ts)

    bars = ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.M15)

    assert len(bars) == 6  # las seis, sin rellenar el hueco


@pytest.mark.integration
def test_incoherent_ohlc_is_rejected_not_repaired(tmp_path: Path) -> None:
    """Un `high` que no envuelve al cierre invalida la fuente, no la barra."""
    close = np.linspace(1.10, 1.11, 8)
    _write(tmp_path, count=8, columns={"high": close - 0.01})

    with pytest.raises(InvariantViolation):
        ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.M15)


@pytest.mark.integration
def test_nan_prices_are_rejected_not_interpolated(tmp_path: Path) -> None:
    close = np.linspace(1.10, 1.11, 8)
    close[3] = np.nan
    _write(tmp_path, count=8, columns={"close": close})

    with pytest.raises(InvariantViolation):
        ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.M15)


# ---------------------------------------------------------------------------
# Fallos de fuente frente a fallos de integridad
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_a_missing_history_is_a_source_error(tmp_path: Path) -> None:
    """La distincion dirige el diagnostico: falta el fichero, no esta corrupto."""
    with pytest.raises(DataSourceError):
        ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("NOEXISTE"), Timeframe.M15)


@pytest.mark.integration
def test_missing_columns_are_an_integrity_error(tmp_path: Path) -> None:
    """El fichero se leyo bien y su contenido no es una serie de precios."""
    _write(tmp_path, count=5, drop=("high",))

    with pytest.raises(DataIntegrityError):
        ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.M15)


@pytest.mark.integration
def test_a_timeframe_without_history_is_a_source_error(tmp_path: Path) -> None:
    """El simbolo existe pero no en ese marco temporal."""
    _write(tmp_path, timeframe=Timeframe.M15)

    with pytest.raises(DataSourceError):
        ParquetMarketData(DatasetLayout(root=tmp_path)).load(Symbol("EURUSD"), Timeframe.H1)


# ---------------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_available_symbols_is_sorted_and_ignores_empty_directories(tmp_path: Path) -> None:
    """El orden se fija en lugar de heredar el del sistema de ficheros.

    En Linux `iterdir` devuelve el orden del directorio, que depende de como se
    creo. Cualquier artefacto derivado de esta lista dejaria de ser reproducible
    entre maquinas.
    """
    for symbol in ("XAUUSD", "EURUSD", "GBPUSD"):
        _write(tmp_path, symbol=symbol, count=3)
    (tmp_path / "SIN_DATOS").mkdir()

    symbols = ParquetMarketData(DatasetLayout(root=tmp_path)).available_symbols()

    assert [str(s) for s in symbols] == ["EURUSD", "GBPUSD", "XAUUSD"]


@pytest.mark.unit
def test_an_absent_root_yields_no_symbols(tmp_path: Path) -> None:
    """Sin directorio de datos no hay simbolos, y no es un error.

    Es el estado de un clon recien hecho: `data/` esta vacio a proposito y la
    plataforma tiene que poder arrancar y decir "no hay historicos" en lugar de
    fallar al inventariarlos.
    """
    assert ParquetMarketData(DatasetLayout(root=tmp_path / "no_existe")).available_symbols() == ()
