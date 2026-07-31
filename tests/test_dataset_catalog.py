"""`ParquetDatasetCatalog`: indexa, no materializa.

El catalogo responde tres preguntas -que series hay, con que huella y de donde
vinieron- y ninguna mas. Lo que mas importa comprobar es que detecte un
artefacto reprocesado: sin eso, apuntaria a unos numeros distintos de los que
anoto y toda comparacion contra resultados anteriores dejaria de ser valida sin
que nadie se enterase.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.core.exceptions import DataSourceError, StorageError
from app.core.types import Timeframe
from app.domain.entities.bars import Bars
from app.research.data.catalog import CATALOG_FILENAME, ParquetDatasetCatalog
from app.research.data.layout import DatasetLayout
from app.research.data.parquet_writer import ParquetMarketDataWriter
from app.shared.ports import DatasetRepositoryPort

pytest.importorskip("pyarrow", reason="El catalogo se apoya en artefactos Parquet")

M15_NS = Timeframe.M15.nanoseconds


def _bars(symbol: str = "EURUSD", *, count: int = 8, base: float = 1.10) -> Bars:
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


def _materialized(root: Path, bars: Bars) -> ParquetDatasetCatalog:
    """Escribe la serie y devuelve el catalogo que la indexa."""
    layout = DatasetLayout(root=root)
    ParquetMarketDataWriter(layout).write(bars, overwrite=True)
    return ParquetDatasetCatalog(layout)


@pytest.mark.unit
def test_the_catalog_satisfies_the_port(tmp_path: Path) -> None:
    assert isinstance(ParquetDatasetCatalog(DatasetLayout(root=tmp_path)), DatasetRepositoryPort)


@pytest.mark.integration
def test_registering_returns_the_content_hash(tmp_path: Path) -> None:
    bars = _bars()
    catalog = _materialized(tmp_path, bars)

    fingerprint = catalog.register(bars, lineage={"provider": "FILE"})

    assert catalog.exists(fingerprint)
    assert dict(catalog.lineage(fingerprint)) == {"provider": "FILE"}


@pytest.mark.integration
def test_registering_agrees_with_the_writer(tmp_path: Path) -> None:
    """Una sola definicion de identidad, dos consumidores.

    Con una huella por pieza, el catalogo acabaria apuntando a un artefacto que
    no reconoce.
    """
    layout = DatasetLayout(root=tmp_path)
    bars = _bars()

    written = ParquetMarketDataWriter(layout).write(bars)
    fingerprint = ParquetDatasetCatalog(layout).register(bars, lineage={})

    assert str(written.content_hash) == fingerprint


@pytest.mark.integration
def test_registering_twice_does_not_duplicate(tmp_path: Path) -> None:
    """Una descarga repetida no debe ensuciar el catalogo."""
    bars = _bars()
    catalog = _materialized(tmp_path, bars)

    first = catalog.register(bars, lineage={"intento": 1})
    second = catalog.register(bars, lineage={"intento": 2})

    assert first == second
    assert len(catalog.entries()) == 1
    assert dict(catalog.lineage(first)) == {"intento": 2}


@pytest.mark.integration
def test_the_series_can_be_recovered(tmp_path: Path) -> None:
    bars = _bars(count=11)
    catalog = _materialized(tmp_path, bars)
    fingerprint = catalog.register(bars, lineage={})

    recovered = catalog.get(fingerprint)

    assert len(recovered) == 11
    assert np.array_equal(recovered.close, bars.close)


@pytest.mark.integration
def test_a_reprocessed_artifact_is_detected(tmp_path: Path) -> None:
    """La comprobacion que da sentido al catalogo.

    Se registra una serie, se sustituye el artefacto por otro distinto bajo el
    mismo nombre, y recuperarlo debe FALLAR en lugar de devolver numeros que ya
    no son los que se anotaron.
    """
    layout = DatasetLayout(root=tmp_path)
    original = _bars(base=1.10)
    ParquetMarketDataWriter(layout).write(original)
    catalog = ParquetDatasetCatalog(layout)
    fingerprint = catalog.register(original, lineage={})

    ParquetMarketDataWriter(layout).write(_bars(base=2.50), overwrite=True)

    with pytest.raises(DataSourceError) as error:
        catalog.get(fingerprint)

    assert "reproceso" in str(error.value)


@pytest.mark.integration
def test_an_unknown_fingerprint_is_reported(tmp_path: Path) -> None:
    catalog = ParquetDatasetCatalog(DatasetLayout(root=tmp_path))

    assert not catalog.exists("noexiste")
    with pytest.raises(DataSourceError):
        catalog.get("noexiste")
    with pytest.raises(DataSourceError):
        catalog.lineage("noexiste")


# ---------------------------------------------------------------------------
# El indice como artefacto
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_index_is_written_with_sorted_keys(tmp_path: Path) -> None:
    """Dos catalogos con el mismo contenido deben producir el mismo fichero.

    Sin orden fijo, cada registro mostraria ruido en el diff y el artefacto
    dejaria de ser comparable.
    """
    layout = DatasetLayout(root=tmp_path)
    writer = ParquetMarketDataWriter(layout)
    catalog = ParquetDatasetCatalog(layout)
    for symbol in ("XAUUSD", "EURUSD"):
        bars = _bars(symbol=symbol)
        writer.write(bars, overwrite=True)
        catalog.register(bars, lineage={"provider": "FILE"})

    raw = json.loads((tmp_path / CATALOG_FILENAME).read_text(encoding="utf-8"))

    assert list(raw) == sorted(raw)


@pytest.mark.integration
def test_the_entry_records_the_range_and_the_lineage(tmp_path: Path) -> None:
    bars = _bars(count=9)
    catalog = _materialized(tmp_path, bars)

    fingerprint = catalog.register(bars, lineage={"provider": "MT5", "requested_start_ns": 0})
    entry = catalog.entries()[fingerprint]

    assert entry["symbol"] == "EURUSD"
    assert entry["timeframe"] == "M15"
    assert entry["bar_count"] == 9
    assert entry["start_ns"] == 0
    assert entry["end_ns"] == 8 * M15_NS
    assert entry["lineage"]["provider"] == "MT5"


@pytest.mark.integration
def test_a_corrupt_index_is_a_storage_error_not_a_crash(tmp_path: Path) -> None:
    """Un JSON roto es un fallo del MEDIO, y su diagnostico es distinto del de
    una serie invalida."""
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / CATALOG_FILENAME).write_text("{esto no es json", encoding="utf-8")
    catalog = ParquetDatasetCatalog(DatasetLayout(root=tmp_path))

    with pytest.raises(StorageError):
        catalog.exists("cualquiera")


@pytest.mark.unit
def test_an_absent_index_is_an_empty_catalog(tmp_path: Path) -> None:
    """Un clon recien hecho no tiene catalogo, y eso no es un error."""
    catalog = ParquetDatasetCatalog(DatasetLayout(root=tmp_path / "vacio"))

    assert catalog.entries() == {}
    assert not catalog.exists("nada")
