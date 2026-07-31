"""Adaptador de `MarketDataWriterPort` sobre Parquet. Serializa y nada mas.

Contrapartida exacta de `parquet_source.py`: aquel lee, este escribe, y ambos
preguntan la ubicacion al mismo `DatasetLayout`, de modo que no pueden divergir.

Lo que este fichero NO hace (ADR-0011):

    no valida        si llega un `Bars`, el tipo ya lo garantizo
    no normaliza     no toca los datos que recibe
    no deduplica     ni fusiona con lo ya escrito
    no descarga      no conoce ninguna fuente
    no cataloga      no conoce `DatasetRepositoryPort`
    no decide rutas  se las pregunta al layout
    no sabe la hora  no conoce proveedor ni version

Todo eso pertenece al servicio de almacenamiento. Aqui solo se convierte una
serie validada en bytes en disco, y se informa de lo que ocurrio.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from app.core.determinism import stable_hash
from app.core.exceptions import StorageError
from app.core.types import ContentHash, Symbol, Timeframe
from app.domain.entities.bars import Bars
from app.domain.value_objects.dataset import WriteResult
from app.research.data.layout import DatasetLayout

#: Columnas del artefacto, en orden fijo. El orden forma parte del formato: dos
#: escrituras del mismo `Bars` deben producir el mismo fichero, y un orden
#: dependiente de un `dict` lo impediria.
COLUMNS: tuple[str, ...] = ("timestamp", "open", "high", "low", "close", "volume")


class ParquetMarketDataWriter:
    """Escribe series en Parquet segun la disposicion que se le inyecta."""

    def __init__(self, layout: DatasetLayout) -> None:
        self._layout = layout

    # -- MarketDataWriterPort -----------------------------------------------

    def exists(self, symbol: Symbol, timeframe: Timeframe) -> bool:
        return self._layout.path_for(symbol, timeframe).is_file()

    def write(self, bars: Bars, *, overwrite: bool = False) -> WriteResult:
        """Persiste la serie y devuelve los hechos tecnicos de la escritura.

        No recibe simbolo ni timeframe: son campos obligatorios de `Bars` desde
        R1a, asi que la serie no puede depositarse bajo una identidad
        equivocada.

        La escritura es ATOMICA -fichero temporal y renombrado-. Un proceso
        interrumpido a mitad dejaria un Parquet truncado que el lector aceptaria
        como serie corta, y una serie corta silenciosa es peor que ninguna:
        produce un backtest que corre y miente.

        Raises:
            StorageError: el destino existe y `overwrite` es `False`, o el medio
                no admitio la escritura.
        """
        target = self._layout.path_for(bars.symbol, bars.timeframe)
        if target.exists() and not overwrite:
            raise StorageError(
                "El historico ya existe y no se pidio sobrescribir",
                symbol=str(bars.symbol),
                timeframe=str(bars.timeframe),
                path=str(target),
            )

        table = self._as_table(bars, target)
        self._write_atomically(table, target)

        return WriteResult(
            content_hash=self._content_hash(bars),
            bytes_written=target.stat().st_size,
            bar_count=len(bars),
            physical_location=str(target),
        )

    # -- interno ------------------------------------------------------------

    @staticmethod
    def _as_table(bars: Bars, target: Path) -> Any:
        """Convierte la serie en una tabla Arrow con las columnas en orden fijo.

        `pyarrow` se importa aqui y no en cabecera para que el modulo pueda
        importarse -y su contrato leerse- en un entorno que no lo tenga.
        """
        try:
            import pyarrow as pa
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise StorageError(
                "Escribir historicos en Parquet requiere pyarrow",
                path=str(target),
                cause=str(exc),
            ) from exc

        # `getattr` y no `bars.field`: aquel resuelve tambien columnas DERIVADAS
        # -typical, median- que no forman parte del artefacto, y no acepta
        # `timestamp`. Aqui se quieren los seis buffers reales y solo esos.
        return pa.table({name: np.asarray(getattr(bars, name)) for name in COLUMNS})

    @staticmethod
    def _write_atomically(table: Any, target: Path) -> None:
        """Escribe en un temporal del mismo directorio y renombra al final.

        Un proceso interrumpido a mitad dejaria un Parquet truncado que el lector
        aceptaria como serie corta, y una serie corta silenciosa produce un
        backtest que corre y miente.

        El temporal va en el directorio de DESTINO y no en el del sistema: un
        renombrado entre volumenes distintos no es atomico y degradaria a copia,
        que es justo la garantia que se busca.
        """
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise StorageError(
                "Escribir historicos en Parquet requiere pyarrow",
                path=str(target),
                cause=str(exc),
            ) from exc

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
            os.close(handle)
            staging = Path(temporary)
            try:
                pq.write_table(table, staging)
                staging.replace(target)
            finally:
                staging.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(
                "El historico no se pudo escribir",
                path=str(target),
                cause=str(exc),
            ) from exc

    @staticmethod
    def _content_hash(bars: Bars) -> ContentHash:
        """Huella del CONTENIDO, no del fichero.

        Se deriva de los datos y de la identidad de la serie, nunca de la ruta ni
        del instante: dos escrituras del mismo `Bars` en sitios distintos deben
        dar la misma huella, y la misma serie reprocesada debe dar una distinta.
        Es lo que permite detectar que los numeros cambiaron bajo el mismo
        nombre.
        """
        return stable_hash(
            {
                "symbol": str(bars.symbol),
                "timeframe": str(bars.timeframe),
                "columns": {
                    name: np.asarray(getattr(bars, name)).tobytes().hex() for name in COLUMNS
                },
            }
        )


__all__ = ["COLUMNS", "ParquetMarketDataWriter"]
