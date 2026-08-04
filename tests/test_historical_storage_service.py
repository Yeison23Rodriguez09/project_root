"""`HistoricalStorageService`: el unico que coordina, y lo hace en orden.

Se prueba con DOBLES de los cuatro puertos, no con las implementaciones reales.
El motivo no es velocidad: con adaptadores reales, un test verde no distinguiria
entre "el servicio orquesta bien" y "los adaptadores compensan un fallo del
servicio". Con dobles se puede afirmar QUE llamo, EN QUE ORDEN y CON QUE.

Al final hay una prueba de integracion con las piezas reales, que comprueba lo
contrario: que el conjunto encaja de verdad.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.application.download.runner import PROVIDERS, build_download_service
from app.application.download.service import HistoricalStorageService
from app.core.exceptions import ConfigValidationError, DataSourceError, StorageError
from app.core.types import ContentHash, Symbol, Timeframe, TimestampNs
from app.domain.entities.bars import Bars
from app.domain.value_objects.dataset import WriteResult
from app.research.data.layout import DatasetLayout
from app.research.data.parquet_writer import ParquetMarketDataWriter

M15_NS = Timeframe.M15.nanoseconds
FIXED_NOW = TimestampNs(1_700_000_000_000_000_000)


def _bars(symbol: str = "EURUSD", *, count: int = 8) -> Bars:
    ts = np.arange(0, count * M15_NS, M15_NS, dtype=np.int64)
    close = np.linspace(1.10, 1.11, count)
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


# ---------------------------------------------------------------------------
# Dobles de los cuatro puertos
# ---------------------------------------------------------------------------


class SourceDouble:
    def __init__(self, bars: Bars | None = None, *, fail: Exception | None = None) -> None:
        self._bars = bars if bars is not None else _bars()
        self._fail = fail
        self.requests: list[tuple[Any, ...]] = []

    def load(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        *,
        start_ns: TimestampNs | None = None,
        end_ns: TimestampNs | None = None,
    ) -> Bars:
        self.requests.append((symbol, timeframe, start_ns, end_ns))
        if self._fail is not None:
            raise self._fail
        return self._bars

    def available_symbols(self) -> Sequence[Symbol]:
        return (Symbol("EURUSD"), Symbol("XAUUSD"))


class WriterDouble:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self._fail = fail
        self.written: list[tuple[Bars, bool]] = []

    def write(self, bars: Bars, *, overwrite: bool = False) -> WriteResult:
        if self._fail is not None:
            raise self._fail
        self.written.append((bars, overwrite))
        return WriteResult(
            content_hash=ContentHash("hash-de-prueba"),
            bytes_written=1234,
            bar_count=len(bars),
            physical_location="/ruta/simulada.parquet",
        )

    def exists(self, symbol: Symbol, timeframe: Timeframe) -> bool:
        return False


class CatalogDouble:
    def __init__(self) -> None:
        self.registered: list[tuple[Bars, Mapping[str, Any]]] = []

    def register(self, bars: Bars, *, lineage: Mapping[str, Any]) -> str:
        self.registered.append((bars, dict(lineage)))
        return "huella-catalogo"

    def get(self, fingerprint: str) -> Bars:  # pragma: no cover - no lo usa el servicio
        raise NotImplementedError

    def lineage(self, fingerprint: str) -> Mapping[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def exists(self, fingerprint: str) -> bool:  # pragma: no cover
        return False


class ClockDouble:
    def __init__(self, at_ns: TimestampNs = FIXED_NOW) -> None:
        self._at = at_ns

    def now_ns(self) -> TimestampNs:
        return self._at


def _service(**overrides: Any) -> tuple[HistoricalStorageService, dict[str, Any]]:
    parts: dict[str, Any] = {
        "source": SourceDouble(),
        "writer": WriterDouble(),
        "catalog": CatalogDouble(),
        "clock": ClockDouble(),
        "provider": "PRUEBA",
    }
    parts.update(overrides)
    return HistoricalStorageService(**parts), parts


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_requested_range_reaches_the_source_untouched() -> None:
    """El servicio no reinterpreta lo que se le pide."""
    service, parts = _service()

    service.download(
        Symbol("EURUSD"),
        Timeframe.M15,
        start_ns=TimestampNs(1_000),
        end_ns=TimestampNs(9_000),
    )

    assert parts["source"].requests == [
        (Symbol("EURUSD"), Timeframe.M15, TimestampNs(1_000), TimestampNs(9_000))
    ]


@pytest.mark.unit
def test_what_the_source_returns_is_what_gets_written() -> None:
    """El servicio no normaliza ni recorta: eso seria reparar por la puerta de atras."""
    bars = _bars(count=13)
    service, parts = _service(source=SourceDouble(bars))

    service.download(Symbol("EURUSD"), Timeframe.M15)

    written, _ = parts["writer"].written[0]
    assert written is bars


@pytest.mark.unit
def test_overwrite_is_forwarded_to_the_writer() -> None:
    service, parts = _service()

    service.download(Symbol("EURUSD"), Timeframe.M15, overwrite=True)

    assert parts["writer"].written[0][1] is True


@pytest.mark.unit
def test_nothing_is_registered_if_the_write_fails() -> None:
    """El catalogo no puede anunciar un artefacto que no existe.

    Registrar antes de escribir dejaria entradas apuntando a ficheros ausentes en
    cuanto el disco se llenara.
    """
    service, parts = _service(writer=WriterDouble(fail=StorageError("disco lleno")))

    with pytest.raises(StorageError):
        service.download(Symbol("EURUSD"), Timeframe.M15)

    assert parts["catalog"].registered == []


@pytest.mark.unit
def test_nothing_is_written_if_the_source_fails() -> None:
    service, parts = _service(source=SourceDouble(fail=DataSourceError("terminal caido")))

    with pytest.raises(DataSourceError):
        service.download(Symbol("EURUSD"), Timeframe.M15)

    assert parts["writer"].written == []
    assert parts["catalog"].registered == []


# ---------------------------------------------------------------------------
# Linaje
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_lineage_records_the_requested_range_not_only_the_obtained_one() -> None:
    """Su diferencia es informacion de primera clase.

    Pedir cinco anos y recibir dos significa que el terminal no tiene mas
    historico descargado. Sin registrarlo, alguien lo leeria despues como que el
    mercado no cotizo.
    """
    service, parts = _service()

    service.download(
        Symbol("EURUSD"),
        Timeframe.M15,
        start_ns=TimestampNs(111),
        end_ns=TimestampNs(999),
    )

    _bars_registered, lineage = parts["catalog"].registered[0]
    assert lineage["requested_start_ns"] == 111
    assert lineage["requested_end_ns"] == 999
    assert lineage["provider"] == "PRUEBA"
    assert lineage["write"]["bytes_written"] == 1234


@pytest.mark.unit
def test_the_download_instant_comes_from_the_injected_clock() -> None:
    """Sin reloj inyectado, el linaje no seria reproducible y ningun test podria
    afirmar nada sobre el."""
    service, parts = _service(clock=ClockDouble(TimestampNs(42)))

    report = service.download(Symbol("EURUSD"), Timeframe.M15)

    assert report.downloaded_at_ns == 42
    assert parts["catalog"].registered[0][1]["downloaded_at_ns"] == 42


@pytest.mark.unit
def test_the_report_gathers_what_each_piece_knows() -> None:
    service, _ = _service(source=SourceDouble(_bars(count=6)))

    report = service.download(Symbol("EURUSD"), Timeframe.M15)

    assert report.bar_count == 6
    assert report.first_ns == 0
    assert report.last_ns == 5 * M15_NS
    assert report.fingerprint == "huella-catalogo"
    assert report.write.bytes_written == 1234
    assert report.provider == "PRUEBA"
    assert "fingerprint" in report.to_dict()


@pytest.mark.unit
def test_an_empty_series_has_no_extremes_and_is_not_an_error() -> None:
    empty = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.array([], dtype=np.int64),
        open=np.array([], dtype=np.float64),
        high=np.array([], dtype=np.float64),
        low=np.array([], dtype=np.float64),
        close=np.array([], dtype=np.float64),
    )
    service, _ = _service(source=SourceDouble(empty))

    report = service.download(Symbol("EURUSD"), Timeframe.M15)

    assert report.bar_count == 0
    assert report.first_ns is None and report.last_ns is None


@pytest.mark.unit
def test_the_service_exposes_the_source_inventory() -> None:
    service, _ = _service()

    assert service.available_symbols() == (Symbol("EURUSD"), Symbol("XAUUSD"))


# ---------------------------------------------------------------------------
# Composicion real
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_real_chain_downloads_writes_and_registers(tmp_path: Path) -> None:
    """Con las piezas reales, y sin terminal: proveedor FILE.

    No es un modo de pruebas. `configs/runtime.toml` exige
    `require_synthetic_data_only` en modo `ci`, asi que un proveedor sin broker
    es un requisito del contrato.
    """
    pytest.importorskip("pyarrow")
    origin = _bars(count=15)
    ParquetMarketDataWriter(DatasetLayout(root=tmp_path)).write(origin)

    service = build_download_service(tmp_path, clock=ClockDouble(), provider="FILE")
    report = service.download(Symbol("EURUSD"), Timeframe.M15, overwrite=True)

    assert report.bar_count == 15
    assert report.provider == "FILE"
    assert report.downloaded_at_ns == FIXED_NOW
    assert Path(report.write.physical_location).is_file()
    assert (tmp_path / "datasets.json").is_file()


@pytest.mark.unit
def test_an_unknown_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError) as error:
        build_download_service(tmp_path, clock=ClockDouble(), provider="BLOOMBERG")

    assert error.value.context["available"] == list(PROVIDERS)


@pytest.mark.unit
def test_the_factory_requires_an_explicit_clock(tmp_path: Path) -> None:
    """`application` no puede importar `container`, donde vive el reloj concreto.

    La restriccion resulta ser la correcta: un defecto que llamara al reloj real
    haria que un test tuviera que acordarse de sustituirlo para ser reproducible.
    """
    import inspect

    clock = inspect.signature(build_download_service).parameters["clock"]

    assert clock.default is inspect.Parameter.empty
