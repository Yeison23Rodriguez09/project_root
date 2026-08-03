"""Convencion de disposicion de los historicos en el repositorio.

Responde a las tres preguntas que ni el lector ni el escritor deben responder por
su cuenta (ADR-0011):

    donde        ubicacion fisica de una serie
    como se llama    naming del artefacto
    como se organiza  estructura de carpetas

Existe para que lector y escritor **no puedan divergir**. Antes, el lector
derivaba la ruta por su cuenta; si el escritor hubiera hecho lo mismo, dos
implementaciones de la misma convencion habrian acabado discrepando en el primer
cambio, y el sintoma seria un historico escrito que nadie encuentra al leer.
Ahora ambos preguntan al mismo objeto.

Se llama `DatasetLayout` y no `...Strategy` a proposito: en esta plataforma
"estrategia" es el concepto central del dominio -`StrategySpec`, `StrategyId`,
el zoo de estrategias- y reutilizar la palabra para una convencion de rutas
crearia una colision de vocabulario en el sitio donde mas cuesta.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.types import Symbol, Timeframe

#: Extension de los artefactos de historico.
SUFFIX = ".parquet"


@dataclass(frozen=True, slots=True)
class DatasetLayout:
    """Un artefacto por simbolo y marco temporal.

        <root>/<SIMBOLO>/<TIMEFRAME>.parquet

    Un fichero por par en lugar de particionado por fecha. Es la forma mas simple
    que satisface el caso de uso -cargar un rango de un instrumento- y evita
    decidir hoy un esquema de particionado que solo puede dimensionarse con
    volumenes reales.

    Cambiarla despues no toca ningun puerto: `MarketDataPort` y
    `MarketDataWriterPort` no mencionan rutas. Es exactamente el motivo de que
    esta clase exista por separado.
    """

    root: Path
    suffix: str = SUFFIX

    def path_for(self, symbol: Symbol, timeframe: Timeframe) -> Path:
        """Ubicacion del artefacto de un par, exista o no."""
        return self.root / str(symbol) / f"{timeframe}{self.suffix}"

    def symbols(self) -> Sequence[Symbol]:
        """Simbolos con al menos un historico, en orden alfabetico.

        El orden se fija en lugar de heredar el del sistema de ficheros: en Linux
        `iterdir` devuelve el orden del directorio, que depende de como se creo, y
        cualquier artefacto derivado de esta lista dejaria de ser reproducible
        entre maquinas.

        El inventario vive aqui y no en el lector porque saber que hay es
        conocimiento de la DISPOSICION: cambiar a particionado por fecha cambia
        como se enumera, y el lector no deberia enterarse.
        """
        if not self.root.is_dir():
            return ()
        return tuple(
            Symbol(child.name)
            for child in sorted(self.root.iterdir(), key=lambda p: p.name)
            if child.is_dir() and any(child.glob(f"*{self.suffix}"))
        )

    def timeframes_for(self, symbol: Symbol) -> Sequence[str]:
        """Marcos temporales disponibles de un simbolo, en orden alfabetico."""
        folder = self.root / str(symbol)
        if not folder.is_dir():
            return ()
        return tuple(sorted(path.stem for path in folder.glob(f"*{self.suffix}") if path.is_file()))


__all__ = ["SUFFIX", "DatasetLayout"]
