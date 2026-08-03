"""`BacktestApplicationService`: el unico que coordina el flujo completo.

    catalogo (DatasetRepositoryPort)        instrumentos (InstrumentCatalogPort)
            |  Bars, ya validadas                    |  Instrument
            v                                        v
                        BacktestEngine
            |  trades + curva de equity
            v
                    analytics.performance_metrics
            |  PerformanceMetrics
            v
                  almacen (ArtifactStorePort)

Aqui vive la SECUENCIA, no la regla. El servicio no calcula un indicador, no
dimensiona una posicion, no decide un llenado y no mide un ratio: pide cada cosa
a quien le corresponde y se ocupa de que el orden y la procedencia sean
correctos. Si empezara a hacer aritmetica de negocio, esa aritmetica
pertenecería a `domain.services`.

Ninguna pieza conoce a las otras. El catalogo no sabe que se va a simular, el
motor no sabe de donde salieron las barras, analytics no sabe quien produjo los
trades y el almacen no sabe que esta guardando. Esa ignorancia mutua es lo que
permite cambiar el formato de los datos sin tocar el motor, y el motor sin tocar
la medida.

Lo que el servicio SI conoce, porque es su trabajo: que la corrida tiene una
identidad derivada de su contenido, y que esa identidad debe quedar escrita
junto al resultado. Un artefacto sin procedencia no es reconstruible, y un
resultado que no se puede repetir no es un experimento (P1).
"""

from __future__ import annotations

from pathlib import Path

from app.analytics import performance_metrics
from app.application.backtest.request import BacktestRequest
from app.application.backtest.response import BacktestReport
from app.monitoring.run_context import RunContext
from app.research.backtest.engine import BacktestEngine, BacktestResult
from app.shared.ports import (
    ArtifactStorePort,
    ClockPort,
    DatasetRepositoryPort,
    EventSinkPort,
    InstrumentCatalogPort,
)

#: Prefijo de los artefactos de una corrida, bajo la raiz del almacen.
ARTIFACT_PREFIX = "backtests"


class BacktestApplicationService:
    """Evalua una estrategia sobre una serie catalogada y deja constancia."""

    def __init__(
        self,
        *,
        catalog: DatasetRepositoryPort,
        instruments: InstrumentCatalogPort,
        engine: BacktestEngine,
        artifacts: ArtifactStorePort,
        events: EventSinkPort,
        clock: ClockPort,
        root: Path,
        config_hash: str,
    ) -> None:
        """Recibe puertos, salvo el motor.

        `engine` entra como clase concreta y no como `BacktestEnginePort` a
        proposito. El puerto promete `(trades, equity)` y el motor produce
        ademas el detalle que hace DESCONFIAR del resultado -barras ambiguas,
        rechazos de riesgo-, que es justo lo que este servicio tiene que sellar
        en el artefacto. Consumirlo por el puerto obligaria a ampliarlo y a que
        todo consumidor cargara con ese detalle, o a perderlo. La sustitucion
        que el puerto protege -otro motor de simulacion- no es una necesidad
        presente, y `application` puede ver `research` por matriz.

        `root` se usa solo para preguntar a git por la version del codigo, que es
        el cuarto componente de la procedencia que P1 exige.
        """
        self._catalog = catalog
        self._instruments = instruments
        self._engine = engine
        self._artifacts = artifacts
        self._events = events
        self._clock = clock
        self._root = root
        self._config_hash = config_hash

    @property
    def _fill_model(self) -> str:
        """Nombre del supuesto de ejecucion vigente, preguntado al simulador.

        Se pregunta en lugar de recibirlo por argumento para que no haya dos
        fuentes: quien compone elige UN simulador, y el nombre sale de el. Un
        parametro paralelo podria decir `OPEN` mientras el motor simula
        `ADVERSE`, y el artefacto mentiria sobre su propia procedencia.
        """
        return self._engine.simulator.name

    def run(self, request: BacktestRequest) -> BacktestReport:
        """Recorre el flujo completo y devuelve el informe de la corrida.

        Raises:
            PlatformError: cualquier fallo previsto de las piezas que compone
                -serie ausente del catalogo, instrumento no declarado, serie mas
                corta que el calentamiento, artefacto no escribible-. No se
                traduce ni se envuelve: el error de cada pieza ya nombra su
                causa, y envolverlo la ocultaria detras de un mensaje generico.
        """
        context = RunContext.create(
            root=self._root,
            config_hash=self._config_hash,
            seed=request.seed,
            at_ns=self._clock.now_ns(),
            strategy_id=str(request.spec.strategy_id),
        ).with_dataset(request.dataset_fingerprint)

        sink = self._events.bind(run_id=str(context.run_id))
        sink.emit(
            "backtest.started",
            strategy_id=context.strategy_id,
            dataset=request.dataset_fingerprint,
            seed=request.seed,
        )

        bars = self._catalog.get(request.dataset_fingerprint)
        instrument = self._instruments.get(request.spec.symbol)
        result = self._engine.run(
            spec=request.spec,
            bars=bars,
            instrument=instrument,
            initial_equity=request.initial_equity,
            seed=request.seed,
        )

        metrics = performance_metrics(
            trades=result.trades,
            equity=result.equity,
            timeframe=request.spec.timeframe,
            initial_equity=result.initial_equity,
        )
        artifacts = self._persist(request, context, result, metrics.to_dict())

        sink.emit(
            "backtest.completed",
            trades=len(result.trades),
            net_profit=result.net_profit,
            artifacts=len(artifacts),
        )
        return BacktestReport(
            run_id=context.run_id,
            strategy_id=str(request.spec.strategy_id),
            dataset_fingerprint=request.dataset_fingerprint,
            metrics=metrics,
            n_trades=len(result.trades),
            final_equity=result.final_equity,
            ambiguous_bars=result.ambiguous_bars,
            rejected_by_risk=result.rejected_by_risk,
            artifacts=artifacts,
        )

    def _persist(
        self,
        request: BacktestRequest,
        context: RunContext,
        result: BacktestResult,
        metrics: dict[str, object],
    ) -> tuple[str, ...]:
        """Escribe resultado y operaciones, y devuelve donde quedaron.

        Dos artefactos y no uno. El resumen se lee entero y se compara con
        `diff`; las operaciones pueden ser millones y van en JSON Lines, que se
        recorre en flujo. Meterlas dentro del resumen obligaria a cargar la
        corrida completa para ver una metrica.

        La ruta cuelga de `RunFingerprint.digest` y del modelo de llenado, y NO
        del `run_id`. La diferencia se descubrio ejecutando: `run_id` se deriva
        de configuracion, codigo, semilla e instante, de modo que con el reloj
        detenido -el que exige la reproducibilidad- dos estrategias distintas
        sobre datos distintos comparten identificador. El `digest` si incluye
        dataset y estrategia, y es, en palabras de su propio contrato, "el
        identificador que responde: es esto exactamente la misma corrida".

        El modelo de llenado va como ultimo tramo en lugar de dentro del hash
        porque las dos ejecuciones del mismo experimento quedan asi una al lado
        de la otra. Compararlas es justo para lo que `ExecutionSimulatorPort`
        declara que existen los dos modelos, y una ruta que lo invita es mejor
        que una que lo esconde detras de un hash distinto.
        """
        base = f"{ARTIFACT_PREFIX}/{context.fingerprint().digest}/{self._fill_model}"
        summary = self._artifacts.write_json(
            f"{base}/result.json",
            {
                "request": request.to_dict(),
                "provenance": dict(context.to_dict()),
                "execution": {"fill_model": self._fill_model},
                "strategy": request.spec.to_dict(),
                "run": result.to_dict(),
                "metrics": metrics,
            },
        )
        trades = self._artifacts.write_table(
            f"{base}/trades.jsonl", [trade.to_dict() for trade in result.trades]
        )
        return (summary, trades)


__all__ = ["ARTIFACT_PREFIX", "BacktestApplicationService"]
