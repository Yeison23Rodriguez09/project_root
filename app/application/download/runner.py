"""Composicion del caso de uso de descarga.

Vive en `application` y no en `container`, y la matriz lo impone: `container` no
puede importar `application`. La division es coherente -el contenedor compone la
PLATAFORMA (reloj, configuracion, bus, catalogos) y `application` compone los
CASOS DE USO a partir de puertos-, y el intento de ponerlo en el contenedor lo
rechazo `test_matrix_is_respected` antes de llegar a ninguna parte.

Es el unico sitio de la cadena de datos que conoce implementaciones concretas.
El servicio recibe puertos y no sabe si detras hay un terminal o un directorio.
"""

from __future__ import annotations

from pathlib import Path

from app.application.download.service import HistoricalStorageService
from app.broker.mt5_source import MT5MarketDataAdapter
from app.core.exceptions import ConfigValidationError
from app.research.data.catalog import ParquetDatasetCatalog
from app.research.data.layout import DatasetLayout
from app.research.data.parquet_source import ParquetMarketData
from app.research.data.parquet_writer import ParquetMarketDataWriter
from app.shared.ports import ClockPort, MarketDataPort

#: Proveedores de historico admitidos por `qp download`.
#:
#: `FILE` no es un modo de pruebas: `configs/runtime.toml` exige
#: `require_synthetic_data_only` en modo `ci`, asi que un proveedor sin broker es
#: un requisito del contrato y no una comodidad.
PROVIDERS: tuple[str, ...] = ("MT5", "FILE")


def build_download_service(
    root: Path,
    *,
    clock: ClockPort,
    provider: str = "MT5",
) -> HistoricalStorageService:
    """Arma la cadena descarga -> escritura -> catalogo para un proveedor.

    Cambiar de proveedor es cambiar un argumento, no una linea del servicio.

    `clock` es OBLIGATORIO y no tiene defecto. `application` no puede importar
    `container` -la matriz lo rechaza- y ahi es donde vive el reloj concreto,
    asi que el instante entra desde la raiz de composicion. La restriccion
    resulta ser la correcta: un defecto que llamara al reloj real haria que un
    test tuviera que recordar sustituirlo para ser reproducible.

    Raises:
        ConfigValidationError: el proveedor pedido no existe.
    """
    normalized = provider.upper()
    if normalized not in PROVIDERS:
        raise ConfigValidationError(
            "Proveedor de historicos desconocido",
            provider=provider,
            available=list(PROVIDERS),
        )

    layout = DatasetLayout(root=root)
    source: MarketDataPort = (
        MT5MarketDataAdapter() if normalized == "MT5" else ParquetMarketData(layout)
    )
    return HistoricalStorageService(
        source=source,
        writer=ParquetMarketDataWriter(layout),
        catalog=ParquetDatasetCatalog(layout),
        clock=clock,
        provider=normalized,
    )


__all__ = ["PROVIDERS", "build_download_service"]
