"""Identidad de una serie historica: una sola definicion, dos consumidores.

La calculan el escritor -para informar de lo que escribio- y el catalogo -para
indexar lo registrado-. Si cada uno tuviera la suya, dos huellas del mismo hecho
acabarian discrepando y el catalogo apuntaria a un artefacto que no reconoce.

Se deriva del CONTENIDO y de la identidad de la serie, nunca de la ruta ni del
instante: dos escrituras del mismo `Bars` dan la misma huella y la misma serie
reprocesada da una distinta. Eso es lo que permite detectar que los numeros
cambiaron bajo el mismo nombre.
"""

from __future__ import annotations

import numpy as np

from app.core.determinism import stable_hash
from app.core.types import ContentHash
from app.domain.entities.bars import Bars

#: Buffers que definen la identidad, en orden fijo. El orden forma parte de la
#: definicion: uno dependiente de un `dict` haria la huella irreproducible.
IDENTITY_COLUMNS: tuple[str, ...] = ("timestamp", "open", "high", "low", "close", "volume")


def content_hash_of(bars: Bars) -> ContentHash:
    """Huella estable del contenido de una serie."""
    return stable_hash(
        {
            "symbol": str(bars.symbol),
            "timeframe": str(bars.timeframe),
            "columns": {
                name: np.asarray(getattr(bars, name)).tobytes().hex() for name in IDENTITY_COLUMNS
            },
        }
    )


__all__ = ["IDENTITY_COLUMNS", "content_hash_of"]
