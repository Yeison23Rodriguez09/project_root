"""`HistoricalStorageService`: el unico que coordina descarga y persistencia.

    fuente (MarketDataPort)
        |  entrega Bars ya validado -si no puede, falla-
        v
    HistoricalStorageService
        |  compone el linaje y decide que se escribe
        v
    escritor (MarketDataWriterPort) -> catalogo (DatasetRepositoryPort)

Ningun adaptador conoce a otro. La fuente no sabe que se persiste, el escritor no
sabe de donde vino la serie y el catalogo no sabe quien la escribio. Esa
ignorancia mutua es lo que permite cambiar de proveedor sin tocar el
almacenamiento, y de formato sin tocar la descarga (ADR-0011).

El servicio no valida a mano: la validacion la hace `Bars` al construirse, y
sucede dentro del adaptador de origen. Si llega aqui un `Bars`, sus invariantes
se cumplen. Repetir las comprobaciones seria duplicar una garantia y dar a
entender que el tipo no basta.
"""

from __future__ import annotations

from app.application.download.response import DownloadReport
from app.core.types import Symbol, Timeframe, TimestampNs
from app.shared.ports import (
    ClockPort,
    DatasetRepositoryPort,
    MarketDataPort,
    MarketDataWriterPort,
)


class HistoricalStorageService:
    """Descarga una serie, la persiste y la registra con su linaje."""

    def __init__(
        self,
        *,
        source: MarketDataPort,
        writer: MarketDataWriterPort,
        catalog: DatasetRepositoryPort,
        clock: ClockPort,
        provider: str,
    ) -> None:
        self._source = source
        self._writer = writer
        self._catalog = catalog
        self._clock = clock
        self._provider = provider

    def available_symbols(self) -> tuple[Symbol, ...]:
        """Simbolos que la fuente publica."""
        return tuple(self._source.available_symbols())

    def download(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        *,
        start_ns: TimestampNs | None = None,
        end_ns: TimestampNs | None = None,
        overwrite: bool = False,
    ) -> DownloadReport:
        """Descarga el rango pedido, lo escribe y lo registra.

        El linaje que se anota incluye el rango PEDIDO ademas del obtenido. La
        diferencia entre ambos es informacion de primera clase: pedir cinco anos
        y recibir dos significa que el terminal no tiene mas historico
        descargado, y sin registrarlo alguien lo interpretaria despues como que
        el mercado no cotizo.

        El instante de la descarga sale del reloj INYECTADO y no de `datetime`:
        es lo que permite que un test fije la hora y que el linaje sea
        reproducible.

        Raises:
            DataSourceError: la fuente no pudo entregar la serie.
            StorageError: el destino ya existe y no se pidio sobrescribir.
            InvariantViolation: la fuente entrego datos que no son una serie.
        """
        bars = self._source.load(symbol, timeframe, start_ns=start_ns, end_ns=end_ns)
        downloaded_at = self._clock.now_ns()
        write = self._writer.write(bars, overwrite=overwrite)

        fingerprint = self._catalog.register(
            bars,
            lineage={
                "provider": self._provider,
                "requested_start_ns": int(start_ns) if start_ns is not None else None,
                "requested_end_ns": int(end_ns) if end_ns is not None else None,
                "downloaded_at_ns": int(downloaded_at),
                "write": write.to_dict(),
            },
        )

        return DownloadReport(
            symbol=symbol,
            timeframe=timeframe,
            bar_count=len(bars),
            first_ns=TimestampNs(int(bars.timestamp[0])) if len(bars) else None,
            last_ns=TimestampNs(int(bars.timestamp[-1])) if len(bars) else None,
            fingerprint=fingerprint,
            write=write,
            provider=self._provider,
            downloaded_at_ns=downloaded_at,
        )


__all__ = ["HistoricalStorageService"]
