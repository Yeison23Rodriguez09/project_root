"""Jerarquia de errores del sistema.

Regla de diseno: cada capa lanza errores de su propia familia. Un fallo de
broker nunca debe emerger como `ValueError` generico en el dominio, porque eso
impide distinguir un bug de calculo de una caida de conectividad.

Todo error lleva un `code` estable. Ese codigo es el que se persiste en logs y
artefactos de auditoria; el mensaje humano puede cambiar, el codigo no.

Coherente con `fail_fast = true`: las condiciones que invalidan un resultado se
lanzan de inmediato en lugar de degradarse a un valor por defecto.
"""

from __future__ import annotations

from typing import Any


class PlatformError(Exception):
    """Raiz de todos los errores propios de la plataforma."""

    code: str = "PLATFORM_ERROR"

    def __init__(self, message: str, /, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context

    def __str__(self) -> str:
        if not self.context:
            return f"[{self.code}] {self.message}"
        detail = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        return f"[{self.code}] {self.message} ({detail})"

    def to_dict(self) -> dict[str, Any]:
        """Representacion serializable para logs estructurados y artefactos."""
        return {"code": self.code, "message": self.message, "context": self.context}


# ---------------------------------------------------------------------------
# Dominio: el estado del mundo viola una regla de negocio o una invariante.
# ---------------------------------------------------------------------------


class DomainError(PlatformError):
    code = "DOMAIN_ERROR"


class InvariantViolation(DomainError):
    """Una entidad se ha construido en un estado imposible.

    Es siempre un bug: indica que un constructor acepto datos que debio
    rechazar, o que se muto un objeto que debia ser inmutable.
    """

    code = "INVARIANT_VIOLATION"


class InsufficientHistory(DomainError):
    """No hay barras suficientes para calcular lo solicitado.

    No es un bug: es una condicion de negocio esperable al inicio de una serie
    o de un fold. Se distingue de `InvariantViolation` precisamente para poder
    filtrarla en la observabilidad sin ocultar errores reales.
    """

    code = "INSUFFICIENT_HISTORY"


# ---------------------------------------------------------------------------
# Datos: la serie de entrada no cumple el contrato de calidad.
# ---------------------------------------------------------------------------


class DataError(PlatformError):
    code = "DATA_ERROR"


class DataIntegrityError(DataError):
    """Duplicados, desorden temporal, OHLC incoherente o NaN en precios."""

    code = "DATA_INTEGRITY_ERROR"


class DataSourceError(DataError):
    """La fuente no pudo entregar los datos (fichero ausente, API caida)."""

    code = "DATA_SOURCE_ERROR"


# ---------------------------------------------------------------------------
# Configuracion: el sistema no puede arrancar de forma determinista.
# ---------------------------------------------------------------------------


class ConfigError(PlatformError):
    code = "CONFIG_ERROR"


class ConfigNotFound(ConfigError):
    code = "CONFIG_NOT_FOUND"


class ConfigValidationError(ConfigError):
    """Un valor existe pero es invalido o incoherente con otro valor."""

    code = "CONFIG_VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Registro: se pidio un bloque, feature o adaptador inexistente.
# ---------------------------------------------------------------------------


class RegistryError(PlatformError):
    code = "REGISTRY_ERROR"


class UnknownComponent(RegistryError):
    code = "UNKNOWN_COMPONENT"


class DuplicateComponent(RegistryError):
    """Dos componentes reclaman el mismo nombre estable.

    Se trata como error fatal en tiempo de importacion: un nombre ambiguo
    rompe la reproducibilidad de cualquier artefacto que lo referencie.
    """

    code = "DUPLICATE_COMPONENT"


# ---------------------------------------------------------------------------
# Ejecucion e infraestructura: el mundo exterior no coopera.
# ---------------------------------------------------------------------------


class ExecutionError(PlatformError):
    code = "EXECUTION_ERROR"


class BrokerError(ExecutionError):
    code = "BROKER_ERROR"


class OrderRejected(ExecutionError):
    """El broker o el validador interno rechazo la orden.

    El contexto debe incluir siempre `reason` para que la auditoria pueda
    explicar el rechazo sin recurrir al log crudo.
    """

    code = "ORDER_REJECTED"


class RiskLimitBreached(ExecutionError):
    """Un limite de riesgo bloqueo la operacion antes de enviarla."""

    code = "RISK_LIMIT_BREACHED"


# ---------------------------------------------------------------------------
# Metodologia: se intento saltar una etapa obligatoria del proceso cientifico.
# ---------------------------------------------------------------------------


class MethodologyError(PlatformError):
    """Violacion del proceso, no del codigo.

    Ejemplos: promover una estrategia sin walk-forward, ejecutar en vivo una
    estrategia en estado `CANDIDATE`, comparar contra un baseline inexistente.
    Se modela como error duro porque es la clase de fallo mas cara del sistema.
    """

    code = "METHODOLOGY_ERROR"


class LookaheadDetected(MethodologyError):
    """Se detecto dependencia de informacion futura.

    Cualquier aparicion en produccion invalida todos los resultados derivados.
    """

    code = "LOOKAHEAD_DETECTED"


class NotValidated(MethodologyError):
    code = "NOT_VALIDATED"


__all__ = [
    "BrokerError",
    "ConfigError",
    "ConfigNotFound",
    "ConfigValidationError",
    "DataError",
    "DataIntegrityError",
    "DataSourceError",
    "DomainError",
    "DuplicateComponent",
    "ExecutionError",
    "InsufficientHistory",
    "InvariantViolation",
    "LookaheadDetected",
    "MethodologyError",
    "NotValidated",
    "OrderRejected",
    "PlatformError",
    "RegistryError",
    "RiskLimitBreached",
    "UnknownComponent",
]
