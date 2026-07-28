"""Jerarquia de excepciones del sistema.

Regla de diseno: cada capa lanza excepciones de su propia familia. Un fallo de
broker nunca debe emerger como `ValueError` generico en el dominio, porque eso
impide distinguir un bug de calculo de una caida de conectividad, y esa
distincion determina si la respuesta correcta es corregir codigo o reintentar.

Toda excepcion lleva un `code` estable. Ese codigo es el que se persiste en
logs estructurados y artefactos de auditoria; el mensaje humano puede
reescribirse, el codigo no.

Coherente con `fail_fast = true` de `pyproject.toml`: las condiciones que
invalidan un resultado se lanzan de inmediato en lugar de degradarse a un valor
por defecto que contaminaria todo lo que venga despues.
"""

from __future__ import annotations

from typing import Any


class PlatformError(Exception):
    """Raiz de todas las excepciones propias de la plataforma.

    Capturar `PlatformError` atrapa cualquier fallo previsto por el sistema y
    deja pasar los errores de programacion de Python (`TypeError`,
    `AttributeError`), que no deben silenciarse nunca.
    """

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
# Validacion y dominio
# ---------------------------------------------------------------------------


class ValidationError(PlatformError):
    """Una entrada no cumple el contrato declarado."""

    code = "VALIDATION_ERROR"


class InvariantViolation(ValidationError):
    """Una entidad se ha construido o mutado hacia un estado imposible.

    Es siempre un bug: indica que un constructor acepto datos que debio
    rechazar, o que se modifico un objeto declarado inmutable.
    """

    code = "INVARIANT_VIOLATION"


class DomainError(PlatformError):
    code = "DOMAIN_ERROR"


class InsufficientHistory(DomainError):
    """No hay barras suficientes para calcular lo solicitado.

    No es un bug: es una condicion esperable al inicio de una serie o de un
    fold. Se distingue de `InvariantViolation` precisamente para poder
    filtrarla en la observabilidad sin ocultar errores reales.
    """

    code = "INSUFFICIENT_HISTORY"


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------


class DataError(PlatformError):
    code = "DATA_ERROR"


class DataIntegrityError(DataError):
    """Duplicados, desorden temporal, OHLC incoherente o NaN en precios."""

    code = "DATA_INTEGRITY_ERROR"


class DataGapError(DataError):
    """Faltan barras mas alla del umbral tolerado para el instrumento.

    Se separa de `DataIntegrityError` porque la respuesta operativa es
    distinta: un hueco puede rellenarse o excluirse del fold, mientras que un
    OHLC incoherente invalida la fuente entera.
    """

    code = "DATA_GAP"


class DataSourceError(DataError):
    """La fuente no pudo entregar los datos (fichero ausente, API caida)."""

    code = "DATA_SOURCE_ERROR"


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------


class ConfigError(PlatformError):
    code = "CONFIG_ERROR"


class ConfigNotFound(ConfigError):
    code = "CONFIG_NOT_FOUND"


class ConfigValidationError(ConfigError):
    """Un valor existe pero es invalido o incoherente con otro valor."""

    code = "CONFIG_VALIDATION_ERROR"


class ProviderUnavailable(ConfigError):
    """Un proveedor de configuracion opcional no puede operar.

    Se distingue de `ConfigNotFound` -que es "el fichero no esta"- porque aqui el
    fichero SI esta y lo que falta es la capacidad de leerlo: una dependencia
    opcional ausente, un formato no soportado.

    No es un `ImportError`. Un `ImportError` propagado desde el interior de un
    proveedor obliga al llamante a conocer los detalles de implementacion para
    decidir si continuar. Con este error el ensamblador decide: si la capa era
    opcional, la omite y lo registra; si era obligatoria, aborta. El sistema
    sigue funcionando sin el proveedor.
    """

    code = "PROVIDER_UNAVAILABLE"


class ProfileCycle(ConfigError):
    """Un perfil de configuracion hereda de si mismo, directa o indirectamente.

    Se detecta antes de resolver. Un ciclo en la herencia de perfiles produciria
    recursion infinita o, peor, una resolucion parcial que parece completa.
    """

    code = "PROFILE_CYCLE"


# Los fallos del catalogo de componentes (`RegistryError`, `UnknownComponent`,
# `DuplicateComponent`, `InvalidParameter`) NO viven aqui: estan en
# `app/core/registry/exceptions.py`, junto al mecanismo al que pertenecen.
# Esta jerarquia solo contiene lo que atraviesa varias capas; si cada subsistema
# volcara sus errores aqui, el fichero acabaria siendo un indice del proyecto
# entero y dejaria de poder leerse.


# ---------------------------------------------------------------------------
# Riesgo y ejecucion
# ---------------------------------------------------------------------------


class RiskViolation(PlatformError):
    """Raiz de los bloqueos por politica de riesgo.

    Una violacion de riesgo no es un error tecnico: es el sistema funcionando.
    Se modela como excepcion para que sea imposible ignorar el retorno.
    """

    code = "RISK_VIOLATION"


class RiskLimitBreached(RiskViolation):
    """Un limite configurado bloqueo la operacion antes de enviarla."""

    code = "RISK_LIMIT_BREACHED"


class MarginInsufficient(RiskViolation):
    code = "MARGIN_INSUFFICIENT"


class ExecutionError(PlatformError):
    code = "EXECUTION_ERROR"


class BrokerError(ExecutionError):
    code = "BROKER_ERROR"


class OrderRejected(ExecutionError):
    """El broker o el validador interno rechazo la orden.

    El contexto debe incluir siempre `reason`, para que la auditoria pueda
    explicar el rechazo sin recurrir al log crudo.
    """

    code = "ORDER_REJECTED"


# ---------------------------------------------------------------------------
# Metodologia: se intento saltar una etapa obligatoria del proceso cientifico
# ---------------------------------------------------------------------------


class MethodologyError(PlatformError):
    """Violacion del proceso, no del codigo.

    Ejemplos: promover una estrategia sin walk-forward, ejecutar en vivo una
    estrategia en estado `CANDIDATE`, comparar contra un baseline inexistente.
    Se modela como error duro porque es la clase de fallo mas cara del sistema:
    no rompe nada visible y produce conclusiones falsas.
    """

    code = "METHODOLOGY_ERROR"


class LookaheadDetected(MethodologyError):
    """Se detecto dependencia de informacion futura.

    Cualquier aparicion invalida todos los resultados derivados.
    """

    code = "LOOKAHEAD_DETECTED"


class NotValidated(MethodologyError):
    """Se intento usar en produccion algo que no supero la validacion."""

    code = "NOT_VALIDATED"


__all__ = [
    "BrokerError",
    "ConfigError",
    "ConfigNotFound",
    "ConfigValidationError",
    "DataError",
    "DataGapError",
    "DataIntegrityError",
    "DataSourceError",
    "DomainError",
    "ExecutionError",
    "InsufficientHistory",
    "InvariantViolation",
    "LookaheadDetected",
    "MarginInsufficient",
    "MethodologyError",
    "NotValidated",
    "OrderRejected",
    "PlatformError",
    "ProfileCycle",
    "ProviderUnavailable",
    "RiskLimitBreached",
    "RiskViolation",
    "ValidationError",
]
