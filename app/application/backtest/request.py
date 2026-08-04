"""Peticion del caso de uso de backtest: todo lo que fija una corrida.

Existe como objeto y no como seis argumentos sueltos porque estos seis campos
son, juntos, lo que hace REPRODUCIBLE la corrida. Sueltos, cada llamante decide
cuales pasa y cuales deja por defecto, y la primera corrida que omita la semilla
deja de poder repetirse sin que nada lo advierta.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation
from app.domain.value_objects.strategy_spec import StrategySpec


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    """Que se evalua, sobre que datos y con que capital.

    Attributes:
        spec: Composicion a evaluar. Objeto de DOMINIO, no una ruta: el servicio
            no sabe si vino de un fichero, de discovery o del zoo, y esa
            ignorancia es lo que permite que los tres recorran el mismo camino.
        dataset_fingerprint: Identidad de la serie en el catalogo. Se pide la
            HUELLA y no el par simbolo/marco temporal a proposito: dos descargas
            del mismo simbolo pueden diferir -reproceso, rango distinto- y una
            corrida que solo declare "EURUSD M15" no es reconstruible.
        initial_equity: Capital de partida de la simulacion.
        seed: Semilla maestra. El recorrido barra a barra es determinista y hoy
            no muestrea nada, pero viaja en la peticion y queda sellada en el
            artefacto: el dia que algo muestree, las corridas anteriores seguiran
            siendo reproducibles porque su semilla esta escrita.
        label: Etiqueta libre del operador, para reconocer la corrida en el
            almacen de artefactos. No participa de ninguna identidad.
    """

    spec: StrategySpec
    dataset_fingerprint: str
    initial_equity: float
    seed: int = 0
    label: str = ""

    def __post_init__(self) -> None:
        if not self.dataset_fingerprint:
            raise InvariantViolation("La corrida necesita una huella de dataset")
        if self.initial_equity <= 0.0:
            raise InvariantViolation(
                "El capital inicial debe ser positivo", initial_equity=self.initial_equity
            )
        if self.seed < 0:
            raise InvariantViolation("La semilla no puede ser negativa", seed=self.seed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": str(self.spec.strategy_id),
            "dataset_fingerprint": self.dataset_fingerprint,
            "initial_equity": self.initial_equity,
            "seed": self.seed,
            "label": self.label,
        }


__all__ = ["BacktestRequest"]
