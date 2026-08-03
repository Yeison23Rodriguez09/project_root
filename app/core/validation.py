"""Resultado de validacion como dato, no como excepcion.

Decision de diseno (ADR-0004): las funciones de validacion NO lanzan por
defecto. Devuelven un `ValidationReport` acumulativo.

Motivo: una excepcion detiene en el primer fallo y pierde el resto del
diagnostico. Cuando se valida una serie de 200.000 barras o el espacio de
busqueda de discovery, lo valioso es el inventario completo de problemas, no el
primero. Quien llama decide si aborta (`raise_if_failed`), degrada o registra.

Esto no contradice `fail_fast = true`: el fallo rapido se aplica a invariantes
rotas, que son bugs y se lanzan. La validacion de datos y configuracion produce
diagnostico completo antes de decidir, porque parar en el primer hueco de una
serie no dice nada sobre la calidad global de esa serie.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import PlatformError
from app.core.types import Severity


@dataclass(frozen=True, slots=True)
class Issue:
    """Un hallazgo unico y auto-explicativo.

    Attributes:
        code: Identificador estable en MAYUSCULAS_CON_GUION_BAJO. Es la clave
            que se agrega en dashboards y auditoria; no debe cambiar nunca.
        message: Explicacion legible para un humano.
        severity: Determina si la corrida puede continuar.
        context: Datos estructurados que permiten localizar el problema
            (indice de barra, timestamp, valor observado, valor esperado).
    """

    code: str
    message: str
    severity: Severity = Severity.ERROR
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        detail = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        suffix = f" ({detail})" if detail else ""
        return f"{self.severity.name}:{self.code}: {self.message}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.name,
            "context": self.context,
        }


@dataclass(slots=True)
class ValidationReport:
    """Inventario acumulativo de hallazgos sobre un objeto validado.

    Es mutable a proposito: se construye incrementalmente durante un recorrido.
    Una vez devuelto al llamante debe tratarse como solo lectura.
    """

    subject: str
    issues: list[Issue] = field(default_factory=list)

    # -- construccion -------------------------------------------------------

    def add(
        self,
        code: str,
        message: str,
        severity: Severity = Severity.ERROR,
        **context: Any,
    ) -> ValidationReport:
        """Registra un hallazgo. Devuelve `self` para permitir encadenamiento."""
        self.issues.append(Issue(code=code, message=message, severity=severity, context=context))
        return self

    def extend(self, other: ValidationReport) -> ValidationReport:
        """Absorbe los hallazgos de otro informe (composicion de validadores)."""
        self.issues.extend(other.issues)
        return self

    # -- consulta -----------------------------------------------------------

    @property
    def ok(self) -> bool:
        """True si no hay nada de severidad ERROR o superior."""
        return self.max_severity < Severity.ERROR

    @property
    def max_severity(self) -> Severity:
        if not self.issues:
            return Severity.INFO
        return max(issue.severity for issue in self.issues)

    def of_severity(self, minimum: Severity) -> list[Issue]:
        return [i for i in self.issues if i.severity >= minimum]

    def codes(self) -> set[str]:
        """Conjunto de codigos presentes. Util en aserciones de test."""
        return {i.code for i in self.issues}

    def count_by_code(self) -> dict[str, int]:
        """Agregado por codigo, ordenado descendentemente por frecuencia."""
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.code] = counts.get(issue.code, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def __len__(self) -> int:
        return len(self.issues)

    def __iter__(self) -> Iterator[Issue]:
        return iter(self.issues)

    def __bool__(self) -> bool:
        """Un informe es "verdadero" si esta limpio.

        Se define explicitamente para evitar la trampa de que un informe vacio
        (correcto) sea falsy por tener longitud cero.
        """
        return self.ok

    # -- escalado -----------------------------------------------------------

    def raise_if_failed(self, exc_type: type[PlatformError]) -> None:
        """Convierte el informe en excepcion si hay errores.

        Se invoca en la frontera de la capa de aplicacion, no dentro de las
        funciones puras de dominio.
        """
        if self.ok:
            return
        failures = self.of_severity(Severity.ERROR)
        summary = "; ".join(str(i) for i in failures[:5])
        if len(failures) > 5:
            summary += f"; (+{len(failures) - 5} mas)"
        raise exc_type(
            f"Validacion fallida de {self.subject}: {summary}",
            subject=self.subject,
            issue_count=len(failures),
            codes=sorted({i.code for i in failures}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "ok": self.ok,
            "max_severity": self.max_severity.name,
            "counts": self.count_by_code(),
            "issues": [i.to_dict() for i in self.issues],
        }


def merge_reports(subject: str, reports: Iterable[ValidationReport]) -> ValidationReport:
    """Fusiona varios informes en uno solo bajo un nuevo sujeto."""
    merged = ValidationReport(subject=subject)
    for report in reports:
        merged.extend(report)
    return merged


__all__ = ["Issue", "ValidationReport", "merge_reports"]
