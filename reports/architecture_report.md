# Reporte de consolidacion arquitectonica

Generado: 2026-07-28T11:35:43Z

- Modulos analizados: **94**
- Aristas entre paquetes: **10**
- Violaciones de la matriz: **0**
- Ciclos de importacion: **0**
- Modulos huerfanos: **64**
- Simbolos duplicados: **1**

## Paquetes

| Paquete | Nivel | Modulos | Dependencias |
|---|---|---|---|
| app.analytics | 3 | 1 | 0 |
| app.application | 5 | 5 | 0 |
| app.broker | 4 | 1 | 0 |
| app.config | 4 | 2 | 1 |
| app.container | 5 | 4 | 3 |
| app.core | 0 | 19 | 0 |
| app.discovery | 3 | 14 | 0 |
| app.domain | 1 | 13 | 1 |
| app.events | 0 | 4 | 1 |
| app.execution | 3 | 1 | 0 |
| app.interfaces | 7 | 5 | 2 |
| app.live | 6 | 1 | 0 |
| app.monitoring | 4 | 1 | 0 |
| app.optimization | 3 | 1 | 0 |
| app.paper | 6 | 1 | 0 |
| app.portfolio | 3 | 1 | 0 |
| app.promotion | 3 | 6 | 0 |
| app.research | 3 | 6 | 0 |
| app.shared | 2 | 2 | 2 |
| app.storage | 4 | 3 | 0 |
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
- `app.application.live`
- `app.application.promotion`
- `app.broker`
- `app.config`
- `app.config.providers.toml`
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
- `app.events`
- `app.execution`
- `app.interfaces`
- `app.interfaces.api`
- `app.interfaces.cli`
- `app.interfaces.cli.doctor`
- `app.interfaces.cli.status`
- `app.live`
- `app.monitoring`
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
- `app.storage.zoo`
- `app.validation`
- `app.walkforward`
## Simbolos duplicados

- `main: app.interfaces.cli, app.interfaces.cli.doctor, app.interfaces.cli.status`
---

Un modulo huerfano no es necesariamente un error: los puntos de entrada y los modulos que solo se importan dinamicamente aparecen aqui. Lo que si es una senal es que crezcan entre dos consolidaciones.
