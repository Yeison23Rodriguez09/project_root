"""Respuesta del caso de uso de descarga."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import Symbol, Timeframe, TimestampNs
from app.domain.value_objects.dataset import WriteResult


@dataclass(frozen=True, slots=True)
class DownloadReport:
    """Que se descargo, donde quedo y bajo que identidad.

    Reune lo que sabe cada pieza -el adaptador de origen, el escritor y el
    catalogo- en un solo objeto, para que la interfaz no tenga que recomponerlo
    y para que el resultado sea auditable sin volver a tocar el disco.

    Attributes:
        symbol / timeframe: Identidad de la serie descargada.
        bar_count: Barras entregadas por la fuente y escritas.
        first_ns / last_ns: Extremos reales de la serie, `None` si vino vacia.
            No coinciden necesariamente con el rango PEDIDO: el proveedor
            entrega lo que tiene, y esa diferencia es informacion.
        fingerprint: Huella de contenido bajo la que quedo registrada.
        write: Hechos tecnicos de la escritura.
        provider: Quien entrego los datos.
        downloaded_at_ns: Instante de la descarga, tomado del reloj INYECTADO.
    """

    symbol: Symbol
    timeframe: Timeframe
    bar_count: int
    first_ns: TimestampNs | None
    last_ns: TimestampNs | None
    fingerprint: str
    write: WriteResult
    provider: str
    downloaded_at_ns: TimestampNs

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": str(self.symbol),
            "timeframe": str(self.timeframe),
            "bar_count": self.bar_count,
            "first_ns": int(self.first_ns) if self.first_ns is not None else None,
            "last_ns": int(self.last_ns) if self.last_ns is not None else None,
            "fingerprint": self.fingerprint,
            "provider": self.provider,
            "downloaded_at_ns": int(self.downloaded_at_ns),
            "write": self.write.to_dict(),
        }


__all__ = ["DownloadReport"]
