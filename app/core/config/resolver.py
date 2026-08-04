"""Motor de precedencia. Funcion pura sobre capas ya cargadas.

No abre ficheros, no lee variables de entorno y no conoce formatos. Recibe
capas ya materializadas en memoria y decide que valor gana, registrando por que.

Esa pureza es lo que permite probar la precedencia exhaustivamente -incluidos
los casos raros de empate y de clave desconocida- sin tocar el sistema de
ficheros. Los proveedores que si leen viven en `app/config/`, nivel
infraestructura, y ningun motor puede importarlos (ADR-0005).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.config.provenance import (
    Origin,
    Priority,
    ResolutionTrace,
    ResolvedValue,
    flatten,
)
from app.core.exceptions import ConfigValidationError
from app.core.types import Severity
from app.core.validation import ValidationReport


@dataclass(frozen=True, slots=True)
class ConfigLayer:
    """Un aporte de configuracion con su procedencia.

    Attributes:
        origin: De donde viene esta capa.
        values: Claves planas con notacion de punto. Si llega un mapa anidado,
            `from_mapping` lo aplana antes de construir la capa.
        lines: Linea de origen por clave, cuando el proveedor puede aportarla.
    """

    origin: Origin
    values: Mapping[str, Any]
    lines: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        priority: Priority,
        locator: str,
        mapping: Mapping[str, Any],
        *,
        lines: Mapping[str, int] | None = None,
    ) -> ConfigLayer:
        """Construye una capa aplanando el mapa recibido."""
        return cls(
            origin=Origin(priority=priority, locator=locator),
            values=flatten(mapping),
            lines=dict(lines or {}),
        )

    def origin_for(self, key: str) -> Origin:
        """Procedencia de una clave concreta, con linea si se conoce."""
        line = self.lines.get(key)
        if line is None:
            return self.origin
        return Origin(priority=self.origin.priority, locator=self.origin.locator, line=line)

    def __len__(self) -> int:
        return len(self.values)


def resolve(
    layers: Sequence[ConfigLayer],
    *,
    known_keys: frozenset[str] | None = None,
) -> tuple[ResolutionTrace, ValidationReport]:
    """Resuelve la configuracion efectiva a partir de varias capas.

    Precedencia: gana la capa de mayor `Priority`. Ante empate exacto de
    prioridad gana la ULTIMA de la secuencia, y el hecho se registra como aviso
    en el informe. No se falla, porque hay casos legitimos -dos ficheros de
    simbolo cargados para un multi-activo-, pero tampoco se silencia: un empate
    no declarado casi siempre significa que dos ficheros se estan pisando sin
    que nadie lo sepa.

    Args:
        layers: Capas en el orden en que se cargaron.
        known_keys: Claves admitidas por el esquema. Si se aporta, cualquier
            clave fuera del conjunto se reporta como error. Una clave
            desconocida suele ser una errata, y aceptarla en silencio hace que
            el sistema ejecute una configuracion distinta de la que el usuario
            cree haber escrito.

    Returns:
        La traza con procedencia y el informe de validacion. El informe se
        devuelve como dato y no se lanza: quien llama decide si un aviso de
        empate aborta o solo se registra.
    """
    report = ValidationReport(subject="configuracion")

    # Orden estable por prioridad. `sorted` de Python es estable, asi que el
    # orden de llegada se conserva dentro de cada prioridad y la resolucion es
    # determinista para una misma secuencia de entrada.
    ordered = sorted(layers, key=lambda layer: int(layer.origin.priority))

    trace = ResolutionTrace()
    for layer in ordered:
        trace.note_source(layer.origin)

    candidates = _collect_candidates(ordered)
    for key in sorted(candidates):
        _resolve_key(trace, report, key, candidates[key])

    if known_keys is not None:
        _report_unknown_keys(trace, report, set(candidates), known_keys)

    return trace, report


def _collect_candidates(
    ordered: Sequence[ConfigLayer],
) -> dict[str, list[tuple[Any, Origin]]]:
    """Propuestas por clave, de menor a mayor prioridad.

    Las capas llegan ya ordenadas, asi que el ultimo elemento de cada lista es
    siempre el ganador. Materializar la lista completa -y no solo el ganador- es
    lo que permite despues explicar por que NO gano el valor que el usuario puso,
    que es la pregunta real al depurar una configuracion.
    """
    candidates: dict[str, list[tuple[Any, Origin]]] = {}
    for layer in ordered:
        for key, value in layer.values.items():
            candidates.setdefault(key, []).append((value, layer.origin_for(key)))
    return candidates


def _resolve_key(
    trace: ResolutionTrace,
    report: ValidationReport,
    key: str,
    proposals: Sequence[tuple[Any, Origin]],
) -> None:
    """Elige el valor de una clave y registra su historia completa."""
    winner_value, winner_origin = proposals[-1]

    if len(proposals) > 1:
        top = int(winner_origin.priority)
        tied = [o for _v, o in proposals[:-1] if int(o.priority) == top]
        if tied:
            report.add(
                "CONFIG_PRIORITY_TIE",
                f"Varias fuentes de igual prioridad fijan {key!r}; gana la ultima",
                Severity.WARNING,
                key=key,
                winner=str(winner_origin),
                tied=[str(o) for o in tied],
            )

    # Los descartados se guardan del mas prioritario al menos, que es el
    # orden en que un humano quiere leerlos al depurar.
    trace.record(
        ResolvedValue(
            key=key,
            value=winner_value,
            origin=winner_origin,
            overridden=tuple(reversed(proposals[:-1])),
        )
    )


def _report_unknown_keys(
    trace: ResolutionTrace,
    report: ValidationReport,
    seen: set[str],
    known_keys: frozenset[str],
) -> None:
    """Denuncia toda clave fuera del esquema, con sugerencia si la hay.

    Se reporta como ERROR y no como aviso: una clave desconocida suele ser una
    errata, y aceptarla en silencio hace que el sistema ejecute una
    configuracion distinta de la que el usuario cree haber escrito.
    """
    for key in sorted(seen - known_keys):
        report.add(
            "CONFIG_UNKNOWN_KEY",
            f"Clave desconocida {key!r}",
            Severity.ERROR,
            key=key,
            source=str(trace.get(key).origin),
            hint=_closest(key, known_keys),
        )


def require(trace: ResolutionTrace, keys: Sequence[str]) -> ValidationReport:
    """Comprueba que existan claves obligatorias sin valor por defecto.

    Se separa de `resolve` porque la obligatoriedad depende del caso de uso: una
    corrida de backtest necesita `data.path` y una de discovery no. Hacerlo
    parte de la resolucion forzaria un esquema unico para todos los modos.
    """
    report = ValidationReport(subject="claves obligatorias")
    for key in keys:
        if key not in trace:
            report.add(
                "CONFIG_MISSING_KEY",
                f"Falta la clave obligatoria {key!r}",
                Severity.ERROR,
                key=key,
            )
        elif trace.get(key).value is None:
            report.add(
                "CONFIG_NULL_KEY",
                f"La clave obligatoria {key!r} esta presente pero vacia",
                Severity.ERROR,
                key=key,
                source=str(trace.get(key).origin),
            )
    return report


def freeze(trace: ResolutionTrace, report: ValidationReport) -> dict[str, Any]:
    """Devuelve la configuracion efectiva o falla.

    Es la frontera: a partir de aqui el sistema trabaja con valores y ya no con
    procedencia. Se invoca desde `application`, nunca desde un motor.

    Raises:
        ConfigValidationError: si el informe contiene errores. Arrancar con una
            configuracion invalida produce resultados que parecen validos, que es
            el fallo mas caro posible.
    """
    report.raise_if_failed(ConfigValidationError)
    return trace.flat()


def _closest(key: str, candidates: frozenset[str]) -> str | None:
    """Sugerencia para una clave mal escrita.

    Heuristica intencionadamente simple: misma cola tras el ultimo punto, o
    prefijo compartido. No se usa distancia de edicion porque no hace falta ser
    listo, hace falta ser util: casi todas las erratas reales son un plural
    sobrante o una seccion equivocada.
    """
    leaf = key.rsplit(".", 1)[-1]
    same_leaf = sorted(c for c in candidates if c.rsplit(".", 1)[-1] == leaf)
    if same_leaf:
        return same_leaf[0]
    section = key.rsplit(".", 1)[0] if "." in key else ""
    same_section = sorted(c for c in candidates if section and c.startswith(f"{section}."))
    return same_section[0] if same_section else None


__all__ = ["ConfigLayer", "freeze", "require", "resolve"]
