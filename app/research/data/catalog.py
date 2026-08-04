"""Catalogo de series: `DatasetRepositoryPort` sobre un indice en disco.

Registra QUE series existen, con que huella y con que linaje. NO escribe los
datos: eso ya lo hizo el escritor, y volver a hacerlo aqui produciria dos
artefactos del mismo hecho. El catalogo indexa y recupera; el escritor
materializa.

    escritor    convierte `Bars` en bytes
    catalogo    dice que esos bytes existen, de donde vinieron y con que huella
    lector      los devuelve como `Bars`

El linaje es obligatorio y no decorativo: un artefacto sin procedencia no es
reconstruible, y seis meses despues -que es cuando se consulta- da igual que
exista. Aqui se guarda lo que el servicio de almacenamiento sepa: proveedor,
rango pedido, instante de la descarga y resultado tecnico de la escritura.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.exceptions import DataSourceError, StorageError
from app.core.types import Symbol, Timeframe
from app.domain.entities.bars import Bars
from app.research.data.identity import content_hash_of
from app.research.data.layout import DatasetLayout
from app.research.data.parquet_source import ParquetMarketData

#: Nombre del indice dentro de la raiz de datos.
CATALOG_FILENAME = "datasets.json"


class ParquetDatasetCatalog:
    """Indice en JSON de las series materializadas bajo un `DatasetLayout`.

    JSON y no una base de datos porque el catalogo de la Fase 4 tiene decenas de
    entradas, se lee entero en microsegundos y se versiona bien en git si alguien
    quiere. Cambiarlo despues no toca `DatasetRepositoryPort`.
    """

    def __init__(self, layout: DatasetLayout, *, reader: ParquetMarketData | None = None) -> None:
        self._layout = layout
        self._reader = reader if reader is not None else ParquetMarketData(layout)

    # -- DatasetRepositoryPort ----------------------------------------------

    def register(self, bars: Bars, *, lineage: Mapping[str, Any]) -> str:
        """Anota la serie en el indice y devuelve su huella.

        Idempotente por construccion: registrar dos veces la misma serie con el
        mismo contenido produce la misma clave y sobrescribe la misma entrada.
        Una descarga repetida no ensucia el catalogo.
        """
        fingerprint = str(content_hash_of(bars))
        index = self._read_index()
        index[fingerprint] = {
            "symbol": str(bars.symbol),
            "timeframe": str(bars.timeframe),
            "bar_count": len(bars),
            "start_ns": int(bars.timestamp[0]) if len(bars) else None,
            "end_ns": int(bars.timestamp[-1]) if len(bars) else None,
            "lineage": dict(lineage),
        }
        self._write_index(index)
        return fingerprint

    def exists(self, fingerprint: str) -> bool:
        return fingerprint in self._read_index()

    def lineage(self, fingerprint: str) -> Mapping[str, Any]:
        entry = self._entry(fingerprint)
        return dict(entry.get("lineage", {}))

    def get(self, fingerprint: str) -> Bars:
        """Devuelve la serie registrada bajo esa huella.

        Comprueba que lo recuperado siga teniendo la huella registrada. Si el
        artefacto se reproceso por detras, el catalogo apuntaria a unos numeros
        distintos de los que anoto, y devolverlos en silencio invalidaria toda
        comparacion contra resultados anteriores sin que nadie se enterase.
        """
        entry = self._entry(fingerprint)
        bars = self._reader.load(Symbol(str(entry["symbol"])), Timeframe(str(entry["timeframe"])))
        actual = str(content_hash_of(bars))
        if actual != fingerprint:
            raise DataSourceError(
                "El artefacto ya no coincide con la huella registrada: se reproceso",
                fingerprint=fingerprint,
                actual=actual,
                symbol=str(entry["symbol"]),
                timeframe=str(entry["timeframe"]),
            )
        return bars

    # -- consulta auxiliar ---------------------------------------------------

    def entries(self) -> Mapping[str, Mapping[str, Any]]:
        """Indice completo, para inventario y diagnostico."""
        return self._read_index()

    # -- interno -------------------------------------------------------------

    @property
    def _path(self) -> Path:
        return self._layout.root / CATALOG_FILENAME

    def _entry(self, fingerprint: str) -> dict[str, Any]:
        index = self._read_index()
        if fingerprint not in index:
            raise DataSourceError("Huella no registrada en el catalogo", fingerprint=fingerprint)
        entry: dict[str, Any] = dict(index[fingerprint])
        return entry

    def _read_index(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(
                "El catalogo de datasets no se pudo leer",
                path=str(self._path),
                cause=str(exc),
            ) from exc
        return dict(loaded)

    def _write_index(self, index: Mapping[str, Any]) -> None:
        """Escribe el indice con claves ordenadas.

        El orden se fija para que dos catalogos con el mismo contenido produzcan
        el mismo fichero: sin el, un diff mostraria ruido en cada registro y el
        artefacto dejaria de ser comparable.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            raise StorageError(
                "El catalogo de datasets no se pudo escribir",
                path=str(self._path),
                cause=str(exc),
            ) from exc


__all__ = ["CATALOG_FILENAME", "ParquetDatasetCatalog"]
