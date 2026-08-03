"""Composicion del caso de uso de backtest.

Vive en `application` y no en `container` por la misma razon que la cadena de
descarga (ADR-0011): la matriz no deja que `container` importe `application`. El
contenedor compone la PLATAFORMA -reloj, configuracion, bus, catalogos-; aqui se
componen los CASOS DE USO a partir de puertos.

Es el unico sitio de esta cadena que conoce implementaciones concretas. El
servicio recibe puertos y no sabe si detras hay un directorio de Parquet o un
doble en memoria.

Una pieza NO se compone aqui y entra por argumento: el catalogo de instrumentos.
Su unico adaptador vive en `app/config`, que es infraestructura de nivel 4 y que
`application` no declara en su `depends` -ni puede: un caso de uso que leyera un
fichero de configuracion introduciria una entrada no declarada y el resultado
dejaria de depender solo de (datos, configuracion, semilla)-. Lo inyecta la raiz
de composicion, que es quien si puede conocerlo.
"""

from __future__ import annotations

from pathlib import Path

from app.application.backtest.service import BacktestApplicationService
from app.core.exceptions import ConfigValidationError
from app.execution.fills import AdverseFillSimulator, OpenFillSimulator
from app.monitoring.sink import NullEventSink
from app.portfolio.limits import RiskLimits
from app.portfolio.policy import FixedFractionalRiskPolicy
from app.research.backtest.engine import BacktestEngine
from app.research.data.catalog import ParquetDatasetCatalog
from app.research.data.layout import DatasetLayout
from app.shared.ports import ArtifactStorePort, ClockPort, InstrumentCatalogPort
from app.storage.artifacts.store import FileArtifactStore

#: Modelos de llenado disponibles, por nombre de linea de comandos.
#:
#: Los dos existen para poder COMPARARLOS: `ExecutionSimulatorPort` declara en su
#: contrato que la diferencia entre un modelo optimista y uno pesimista es una
#: medida directa de la fragilidad de la estrategia frente a la calidad de
#: ejecucion. Ofrecer solo uno convertiria esa medida en un ejercicio manual que
#: nadie hace.
FILL_MODELS: tuple[str, ...] = ("OPEN", "ADVERSE")


def build_backtest_service(
    root: Path,
    *,
    clock: ClockPort,
    instruments: InstrumentCatalogPort,
    config_hash: str,
    limits: RiskLimits,
    data_root: Path | None = None,
    artifacts: ArtifactStorePort | None = None,
    fill_model: str = "OPEN",
) -> BacktestApplicationService:
    """Arma la cadena catalogo -> motor -> analytics -> artefactos.

    Args:
        root: Raiz del proyecto. Se usa para preguntar a git la version del
            codigo, que es parte de la procedencia que P1 exige.
        clock: Reloj inyectado. Sin defecto y sin reloj real de reserva: el
            instante de una corrida entra desde la raiz de composicion, que es
            lo unico que hace repetible el `run_id`.
        instruments: Catalogo de instrumentos, inyectado por la razon que explica
            el docstring del modulo.
        config_hash: Huella de la configuracion efectiva, para sellar la corrida.
        limits: Politica de riesgo YA RESUELTA y tipada. Sin defecto y sin
            reserva: desde ADR-0015 sale de `configs/risk.toml` a traves de la
            raiz de composicion. Un defecto aqui volveria a crear la segunda
            fuente que ese ADR retiro, y lo haria de la peor manera -el fichero
            documentado quedaria de adorno y mandaria el codigo-.
        data_root: Raiz de los historicos. Por defecto `root/data`, que es donde
            los deja `qp download`.
        artifacts: Almacen de artefactos. Por defecto en disco bajo
            `root/artifacts`.
        fill_model: Cual de los dos modelos de llenado se usa.

    Raises:
        ConfigValidationError: el modelo de llenado pedido no existe.
    """
    layout = DatasetLayout(root=data_root if data_root is not None else root / "data")
    return BacktestApplicationService(
        catalog=ParquetDatasetCatalog(layout),
        instruments=instruments,
        engine=BacktestEngine(
            simulator=_simulator(fill_model),
            risk=FixedFractionalRiskPolicy(limits),
        ),
        artifacts=artifacts if artifacts is not None else FileArtifactStore(root / "artifacts"),
        events=NullEventSink(),
        clock=clock,
        root=root,
        config_hash=config_hash,
    )


def _simulator(fill_model: str) -> OpenFillSimulator | AdverseFillSimulator:
    """Elige el modelo de llenado, o rechaza el nombre.

    Se rechaza con un error de configuracion en lugar de caer al modelo
    optimista por defecto. Un nombre mal escrito que produjera silenciosamente
    la simulacion MAS favorable es la peor forma posible de fallar.
    """
    normalized = fill_model.upper()
    if normalized not in FILL_MODELS:
        raise ConfigValidationError(
            "Modelo de llenado desconocido",
            fill_model=fill_model,
            available=list(FILL_MODELS),
        )
    return OpenFillSimulator() if normalized == "OPEN" else AdverseFillSimulator()


__all__ = ["FILL_MODELS", "build_backtest_service"]
