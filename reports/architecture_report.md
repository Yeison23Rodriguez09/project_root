# Reporte de consolidacion arquitectonica

Generado por `scripts/consolidate_architecture.py` desde `configs/architecture.toml` v3

- Modulos analizados: **122**
- Aristas entre paquetes: **25**
- Violaciones de la matriz: **0**
- Ciclos de importacion: **0**
- Modulos huerfanos: **76**
- Simbolos duplicados: **1**

## Paquetes

| Paquete | Nivel | Modulos | Dependencias |
|---|---|---|---|
| app.analytics | 3 | 1 | 0 |
| app.application | 5 | 9 | 5 |
| app.broker | 4 | 2 | 2 |
| app.config | 4 | 8 | 2 |
| app.container | 5 | 6 | 4 |
| app.core | 0 | 19 | 0 |
| app.discovery | 3 | 14 | 0 |
| app.domain | 1 | 15 | 1 |
| app.events | 0 | 4 | 1 |
| app.execution | 3 | 1 | 0 |
| app.interfaces | 7 | 9 | 3 |
| app.live | 6 | 1 | 0 |
| app.monitoring | 4 | 4 | 2 |
| app.optimization | 3 | 1 | 0 |
| app.paper | 6 | 1 | 0 |
| app.portfolio | 3 | 1 | 0 |
| app.promotion | 3 | 6 | 0 |
| app.research | 3 | 11 | 2 |
| app.shared | 2 | 2 | 2 |
| app.storage | 4 | 4 | 1 |
| app.validation | 3 | 1 | 0 |
| app.walkforward | 3 | 1 | 0 |

## Violaciones de la matriz

_ninguno_

## Ciclos

_ninguno_

## Huerfanos

- `app.analytics`
- `app.application`
- `app.application.backtest`
- `app.application.discovery`
- `app.application.download`
- `app.application.live`
- `app.application.promotion`
- `app.broker`
- `app.config`
- `app.config.instruments`
- `app.config.providers`
- `app.config.providers.yaml`
- `app.container`
- `app.core`
- `app.core.config`
- `app.core.math`
- `app.core.registry`
- `app.discovery`
- `app.discovery.catalog`
- `app.discovery.constraints`
- `app.discovery.evaluation`
- `app.discovery.evolution`
- `app.discovery.evolution.crossover`
- `app.discovery.evolution.elitism`
- `app.discovery.evolution.mutation`
- `app.discovery.evolution.selection`
- `app.discovery.generator`
- `app.discovery.graph`
- `app.discovery.pruning`
- `app.discovery.scoring`
- `app.discovery.serialization`
- `app.domain`
- `app.domain.entities`
- `app.domain.services`
- `app.domain.value_objects`
- `app.domain.value_objects.money`
- `app.domain.value_objects.time_range`
- `app.domain.value_objects.validation_metrics`
- `app.events`
- `app.execution`
- `app.interfaces`
- `app.interfaces.api`
- `app.interfaces.cli`
- `app.interfaces.cli.config_cmd`
- `app.interfaces.cli.doctor_cmd`
- `app.interfaces.cli.download_cmd`
- `app.interfaces.cli.plugins_cmd`
- `app.interfaces.cli.preflight_cmd`
- `app.interfaces.cli.status_cmd`
- `app.live`
- `app.monitoring`
- `app.monitoring.run_context`
- `app.monitoring.runtime_metrics`
- `app.monitoring.sink`
- `app.optimization`
- `app.paper`
- `app.portfolio`
- `app.promotion`
- `app.promotion.approval`
- `app.promotion.archive`
- `app.promotion.deployment`
- `app.promotion.eligibility`
- `app.promotion.ranking`
- `app.research`
- `app.research.backtest`
- `app.research.data`
- `app.research.features`
- `app.research.signals`
- `app.research.strategies`
- `app.shared`
- `app.storage`
- `app.storage.artifacts`
- `app.storage.artifacts.store`
- `app.storage.zoo`
- `app.validation`
- `app.walkforward`
## Simbolos duplicados

- `main: app.interfaces.cli, app.interfaces.cli.config_cmd, app.interfaces.cli.doctor_cmd, app.interfaces.cli.download_cmd, app.interfaces.cli.plugins_cmd, app.interfaces.cli.preflight_cmd, app.interfaces.cli.status_cmd`
---

Un modulo huerfano no es necesariamente un error: los puntos de entrada y los modulos que solo se importan dinamicamente aparecen aqui. Lo que si es una senal es que crezcan entre dos consolidaciones.
