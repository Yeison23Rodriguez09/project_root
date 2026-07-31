"""`ParquetMarketDataWriter`: serializa y nada mas.

Como en el lector, los tests que mas valen son los de lo que NO hace. Y hay dos
propiedades que solo se ven aqui: que la escritura sea atomica, y que la huella
dependa del contenido y no del sitio ni del momento.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.core.exceptions import StorageError
from app.core.types import Symbol, Timeframe
from app.domain.entities.bars import Bars
from app.research.data.layout import DatasetLayout
from app.research.data.parquet_source import ParquetMarketData
from app.research.data.parquet_writer import ParquetMarketDataWriter
from app.shared.ports import MarketDataWriterPort

pytest.importorskip("pyarrow", reason="El escritor de Parquet requiere pyarrow")

M15_NS = Timeframe.M15.nanoseconds


def _bars(symbol: str = "EURUSD", *, count: int = 10, base: float = 1.10) -> Bars:
    ts = np.arange(0, count * M15_NS, M15_NS, dtype=np.int64)
    close = np.linspace(base, base + 0.001 * count, count)
    return Bars.from_arrays(
        symbol=symbol,
        timeframe=Timeframe.M15,
        timestamp=ts,
        open=close - 0.00005,
        high=close + 0.00020,
        low=close - 0.00020,
        close=close,
        volume=np.full(count, 100.0),
    )


@pytest.mark.unit
def test_the_writer_satisfies_the_port(tmp_path: Path) -> None:
    assert isinstance(
        ParquetMarketDataWriter(DatasetLayout(root=tmp_path)), MarketDataWriterPort
    )


@pytest.mark.integration
def test_writing_reports_the_technical_facts(tmp_path: Path) -> None:
    writer = ParquetMarketDataWriter(DatasetLayout(root=tmp_path))

    result = writer.write(_bars(count=12))

    assert result.bar_count == 12
    assert result.bytes_written > 0
    assert Path(result.physical_location).is_file()
    assert str(result.content_hash)


@pytest.mark.integration
def test_the_writer_never_receives_symbol_or_timeframe(tmp_path: Path) -> None:
    """La identidad viaja DENTRO de la serie desde R1a.

    Es lo que impide depositar un historico bajo una identidad equivocada: no hay
    argumento con el que confundirse.
    """
    writer = ParquetMarketDataWriter(DatasetLayout(root=tmp_path))

    result = writer.write(_bars(symbol="XAUUSD"))

    assert Path(result.physical_location).parent.name == "XAUUSD"


@pytest.mark.integration
def test_the_round_trip_returns_the_same_series(tmp_path: Path) -> None:
    """Lo escrito y lo leido son la misma serie, bit a bit."""
    layout = DatasetLayout(root=tmp_path)
    original = _bars(count=20)

    ParquetMarketDataWriter(layout).write(original)
    recovered = ParquetMarketData(layout).load(original.symbol, original.timeframe)

    assert len(recovered) == len(original)
    for column in ("timestamp", "open", "high", "low", "close", "volume"):
        assert np.array_equal(getattr(recovered, column), getattr(original, column))


# ---------------------------------------------------------------------------
# Sobrescritura: no ocurre por descuido
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_an_existing_history_is_not_replaced_by_accident(tmp_path: Path) -> None:
    writer = ParquetMarketDataWriter(DatasetLayout(root=tmp_path))
    writer.write(_bars())

    with pytest.raises(StorageError) as error:
        writer.write(_bars())

    assert "sobrescribir" in str(error.value)


@pytest.mark.integration
def test_overwrite_replaces_the_content(tmp_path: Path) -> None:
    layout = DatasetLayout(root=tmp_path)
    writer = ParquetMarketDataWriter(layout)
    writer.write(_bars(base=1.10))

    writer.write(_bars(base=2.50), overwrite=True)

    recovered = ParquetMarketData(layout).load(Symbol("EURUSD"), Timeframe.M15)
    assert float(recovered.close[0]) == pytest.approx(2.50)


@pytest.mark.integration
def test_exists_reports_what_is_on_disk(tmp_path: Path) -> None:
    writer = ParquetMarketDataWriter(DatasetLayout(root=tmp_path))

    assert not writer.exists(Symbol("EURUSD"), Timeframe.M15)
    writer.write(_bars())
    assert writer.exists(Symbol("EURUSD"), Timeframe.M15)
    assert not writer.exists(Symbol("EURUSD"), Timeframe.H1)


# ---------------------------------------------------------------------------
# La huella identifica el contenido
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_same_series_written_twice_has_the_same_hash(tmp_path: Path) -> None:
    """La huella no depende del sitio ni del momento.

    Si dependiera, dos escrituras del mismo dato pareceria que difieren y la
    deteccion de reprocesamiento se volveria ruido.
    """
    first = ParquetMarketDataWriter(DatasetLayout(root=tmp_path / "a")).write(_bars())
    second = ParquetMarketDataWriter(DatasetLayout(root=tmp_path / "b")).write(_bars())

    assert first.content_hash == second.content_hash
    assert first.physical_location != second.physical_location


@pytest.mark.integration
def test_a_reprocessed_series_has_a_different_hash(tmp_path: Path) -> None:
    """Es la propiedad que delata que los numeros cambiaron bajo el mismo nombre."""
    writer = ParquetMarketDataWriter(DatasetLayout(root=tmp_path))

    original = writer.write(_bars(base=1.10))
    reprocessed = writer.write(_bars(base=1.11), overwrite=True)

    assert original.content_hash != reprocessed.content_hash


@pytest.mark.integration
def test_the_hash_distinguishes_instruments(tmp_path: Path) -> None:
    """Dos instrumentos con precios identicos no son la misma serie."""
    writer = ParquetMarketDataWriter(DatasetLayout(root=tmp_path))

    eur = writer.write(_bars(symbol="EURUSD"))
    xau = writer.write(_bars(symbol="XAUUSD"))

    assert eur.content_hash != xau.content_hash


# ---------------------------------------------------------------------------
# Atomicidad
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_no_temporary_files_survive_a_successful_write(tmp_path: Path) -> None:
    layout = DatasetLayout(root=tmp_path)
    ParquetMarketDataWriter(layout).write(_bars())

    assert list((tmp_path / "EURUSD").glob("*.tmp")) == []


@pytest.mark.integration
def test_a_failed_write_leaves_no_partial_artifact(tmp_path: Path) -> None:
    """Un Parquet truncado seria aceptado por el lector como serie corta.

    Y una serie corta silenciosa produce un backtest que corre y miente, que es
    peor que uno que no corre.
    """
    layout = DatasetLayout(root=tmp_path)
    writer = ParquetMarketDataWriter(layout)
    target = layout.path_for(Symbol("EURUSD"), Timeframe.M15)
    target.parent.mkdir(parents=True, exist_ok=True)

    class Explosivo:
        def __getattr__(self, name: str) -> object:
            raise OSError("disco lleno")

    with pytest.raises(StorageError):
        writer._write_atomically(Explosivo(), target)

    assert not target.exists()
    assert list(target.parent.glob("*.tmp")) == []
