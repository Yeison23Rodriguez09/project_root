Baseline verificado. Ahora lanzo la auditoría orquestada sobre las 12 dimensiones.

Auditoría orquestada en marcha (12 dimensiones + refutación adversarial). Mientras corre, leo yo mismo los contratos que gobiernan todo, para no firmar un informe basado solo en lo que me reporten.

El workflow está limitado a 2 agentes concurrentes (4 cores), así que seguirá un rato. Continúo mi propia auditoría en paralelo — la raíz de composición es lo que decide qué existe de verdad.

El workflow avanza a 2 agentes por el límite de CPU; sigo cubriendo el resto yo mismo, que es más rápido.

El workflow lleva ~50 min sin cerrar un solo agente (tope de 2 concurrentes con 4 cores); a ese ritmo son horas. Lo detengo y termino la cobertura yo mismo, que va mucho más rápido.

Auditoría completa. Detuve el workflow orquestado tras ~50 min sin cierres (tope de 2 agentes con 4 cores) y completé las 12 fases yo mismo ejecutando los comandos de primera mano — todo lo que sigue lo he verificado personalmente.

---

# INFORME DE AUDITORÍA ARQUITECTÓNICA — QuantPlatform

**Fecha:** 2026-07-29 · **Commit:** `0f62d48` · **Rama:** `main` · **Árbol de trabajo:** 2 ficheros modificados sin commitear

**Convención de evidencia:** `[HECHO]` = verificado por mí ejecutando el comando o leyendo el fichero · `[HEUR]` = derivado de análisis estático aproximado · `[NV]` = no verificable.

---

## 1. Resumen ejecutivo

QuantPlatform es un proyecto de **gobernanza arquitectónica excepcionalmente disciplinada** con **una plataforma parcialmente construida y prácticamente nada conectado**. No es un proyecto en mal estado: es un proyecto honesto que declara su propio estado con precisión inusual. Los problemas reales no son de calidad de código —`mypy --strict` está limpio sobre 109 ficheros y `ruff` pasa— sino de **cierre de bucle**: piezas correctas que nadie invoca.

Cinco hechos definen el estado:

1. **14.249 LOC, 109 módulos bajo `app/`, 1.821 tests, 1 falla.** `[HECHO]`
2. **13 de los 22 paquetes declarados están vacíos** (solo docstring). Los 9 con código son `core`, `domain`, `shared`, `events`, `config`, `container`, `monitoring`, `storage`, `interfaces`. Todos los motores (research, discovery, validation, portfolio, execution, promotion, analytics, walkforward, optimization, broker, paper, live, application) están vacíos. `[HECHO]`
3. **De 22 puertos declarados en `app/shared/ports.py`, exactamente 1 se registra en el contenedor** (`ClockPort`). `[HECHO]`
4. **El criterio DoD `ci_green` es `blocking` para toda capacidad y no existe ningún pipeline** — ni `.github/`, ni `.pre-commit-config.yaml`, ni `Makefile`, ni `tox.ini`. Consecuencia estructural: **ninguna capacidad puede alcanzar `ready`, por lo que la Fase 4 no puede abrirse nunca por construcción.** `[HECHO]`
5. **`qp doctor` —el criterio de aceptación literal de la Fase 3— revienta en la consola por defecto de Windows**, el sistema operativo del propio proyecto. `[HECHO]`

**NO EXISTE CÓDIGO R EN ESTE PROYECTO.** La Fase 3 del encargo (flujo Python↔R) no tiene sujeto. Detalle y evidencia en §3.

**Contradicción arquitectónica que exige tu decisión antes de tocar nada** (§7, hallazgo C-3): `scripts/generate_docs.py` estampa la fecha del día en un artefacto derivado cuyo test exige igualdad byte a byte. El criterio bloqueante `docs_regenerated` se pone rojo **cada cambio de día UTC** sin que nada haya cambiado. Es la única falla de la suite ahora mismo, y es un defecto de diseño del contrato P7, no un despiste.

---

## 2. Arquitectura reconstruida

### 2.1 Jerarquía de gobierno (real, verificada)

```
CONSTITUTION.md          P1..P10, principios irrenunciables
      ↓ gobierna
configs/*.toml           6 contratos + 1 perfil + 2 símbolos
      ↓ gobierna
app/                     109 módulos, 22 paquetes declarados
      ↑ verifica
tests/                   11 ficheros, 1.821 instancias de test
```

`[HECHO]` Los 10 principios están citados por al menos un contrato; `test_every_principle_is_invoked` y `test_every_citation_resolves` pasan. El mecanismo constitucional funciona — con una excepción (§7, A-9).

### 2.2 Capas y rango (`configs/architecture.toml:116-124`)

```
core 0 → domain 1 → shared 2 → engine 3 → infrastructure 4
     → application 5 → operation 6 → interface 7
```

### 2.3 Grafo de paquetes — estado real de implementación

```
capa 0  core ●16      events ●3
capa 1  domain ●9
capa 2  shared ●1  (ports.py, 22 Protocol)
capa 3  research ○   analytics ○   portfolio ○   execution ○
        walkforward ○  validation ○  optimization ○  discovery ○  promotion ○
capa 4  config ●6    broker ○   storage ●1   monitoring ●3
capa 5  container ●5   application ○
capa 6  paper ○   live ○
capa 7  interfaces ●5  (cli ●5, api ○)

● = con código (nº de módulos)      ○ = vacío, solo docstring
```

`[HECHO]` Toda la capa 3 (motores) y la capa 6 (operación) están vacías. `app/application/` —la capa que debe componer casos de uso— está vacía con 4 subpaquetes vacíos.

### 2.4 Lo que realmente compone la plataforma

`app/container/bootstrap.py:189-234` — `build_platform()` registra **exactamente 6 componentes**:

| Componente | Puerto | Implementación | Dónde se declara el puerto |
|---|---|---|---|
| `clock` | `ClockPort` | `SystemClock` / `FrozenClock` según modo | `app/shared/ports.py:47` |
| `config` | `ConfigPort` | `ResolvedConfig` | `bootstrap.py:72` ⚠️ |
| `event_bus` | `EventBusPort` | `EventBus` + `Recorder` enganchado | `bootstrap.py:51` ⚠️ |
| `registry.feature` | `FeatureRegistryPort` (generado) | `Registry("feature")` **vacío** | `bootstrap.py:282` (dinámico) |
| `registry.signal` | `SignalRegistryPort` (generado) | `Registry("signal")` **vacío** | ídem |
| `registry.strategy` | `StrategyRegistryPort` (generado) | `Registry("strategy")` **vacío** | ídem |

`[HECHO]` Confirmado ejecutando `qp status`: `componentes 6`, `arranque clock -> config -> registry.feature -> registry.signal -> registry.strategy -> event_bus`.

⚠️ **Dos puertos viven fuera de `shared/`**: `ConfigPort` y `EventBusPort` se declaran en `bootstrap.py` (capa application). Está justificado en prosa (`bootstrap.py:44-46`: «son piezas de la plataforma, no fronteras del dominio»), pero significa que la declaración de puertos tiene dos ubicaciones.

### 2.5 Cadena de resolución de configuración

`bootstrap.py:166-186`, precedencia ascendente:

```
configs/global.toml (GLOBAL_FILE) → configs/profiles/{mode}.toml (PROFILE_FILE)
→ configs/local.toml (LOCAL_FILE) → entorno QP_* → CLI → overrides
```

`[HECHO]` **La configuración efectiva en modo `research` tiene 2 claves.** Verificado con `qp config show`:

```
huella  sha256:dc038bf3e62f0fd5
claves  2
  config.hot_reload = False  <- configs/global.toml
  meta.version = 1           <- configs/global.toml
```

En modo `ci`, 3 claves. `configs/profiles/research.toml` **no existe** (solo `ci.toml`), y `research` es el modo por defecto (`runtime.toml:21`).

### 2.6 Ciclo de vida

`LifecyclePort` (`ports.py:247-282`) declara 6 fases: `initialize → load → warmup → start → stop → dispose`. El contenedor las ejecuta en orden topológico. `[HECHO]` **`build_platform` devuelve el contenedor sellado y nadie llama a `start()`.** `delivery.toml:280-285` lo reconoce textualmente: *«las seis fases están implementadas y probadas… pero ningún componente real de la plataforma implementa todavía un hook»*.

---

## 3. Flujo completo Python ↔ R

**NO EXISTE. El proyecto es 100 % Python.** `[HECHO]`

Evidencia:

| Comprobación | Resultado |
|---|---|
| `git ls-files \| grep -Ei '\.(R\|Rmd\|Rproj\|rds\|Rdata)$\|renv\|DESCRIPTION\|NAMESPACE'` | 0 resultados |
| `find . -iname '*.R' -o -iname '*.Rmd' -o -iname 'renv.lock'` (excluyendo `.git`) | 2 resultados, **ambos dentro de `.venv/`** y de terceros: `pre_commit/resources/empty_template_activate.R` y `scipy/stats/tests/.../generate_fisher_exact_results_from_r.R` |
| `rpy2`, `reticulate` en el código o en `pyproject.toml` | 0 |
| `subprocess` que invoque `Rscript` | 0 |

Los únicos usos de `subprocess` en el primer plano son: `app/monitoring/run_context.py:52,65` (lee `git HEAD` y estado dirty), `scripts/consolidate_architecture.py:284,440,454` (git, compileall, pytest) y `tests/test_determinism.py:120,157` (relanza el intérprete para comparar huellas entre procesos). Ninguno cruza a otro lenguaje.

**Conclusión:** si esperabas un componente en R, no está en el repositorio. Si el encargo asumía un sistema híbrido, esa premisa es incorrecta para este proyecto — y las secciones 4-R y los riesgos de integración entre lenguajes quedan sin sujeto, no vacías por omisión mía.

---

## 4. Inventario de dependencias

### 4.1 Python — declarado frente a usado

`[HECHO]` Imports de terceros **realmente presentes** en `app/`, `scripts/`, `tests/`, `main.py`: **únicamente `numpy`, `orjson`, `structlog`, `yaml`, `pytest`**.

| Paquete | Declarado en `pyproject.toml` | Importado | Veredicto |
|---|---|---|---|
| `numpy>=2.0` | `[project].dependencies` | sí (7 módulos) | ✅ correcto |
| `orjson>=3.10` | `[project].dependencies` | sí (2: `monitoring/sink.py`, y serialización) | ✅ correcto |
| `structlog>=24` | `[project].dependencies` | sí (**1 solo**: `app/monitoring/sink.py:30`) | ✅ correcto, uso mínimo |
| `scipy>=1.14` | `[project].dependencies` | **no** | ⚠️ sin uso |
| `pandas>=2.2` | `[project].dependencies` | **no** (solo en comentarios) | ⚠️ sin uso |
| `pyarrow>=18` | `[project].dependencies` | **no** | ⚠️ sin uso |
| `pydantic>=2.10` | `[project].dependencies` | **no** | ⚠️ sin uso **y prohibido** en core/domain/shared/motores por `architecture.toml:581-592` |
| `pydantic-settings>=2.6` | `[project].dependencies` | **no** | ⚠️ sin uso |
| `typer>=0.15` | `[project].dependencies` | **no** (la CLI usa `argparse`) | ⚠️ sin uso |
| **`PyYAML`** | **no declarado** | **sí** (`app/config/providers/yaml.py:52`) | ⚠️ dependencia oculta |
| `MetaTrader5` | extra `mt5` | no | ✅ correcto (fase 10) |
| `matplotlib`, `plotly` | extra `viz` | no | ✅ correcto |
| `jupyterlab`, `ipykernel` | extra `research` | no | ✅ correcto |

Sobre `PyYAML`: el import está **deliberadamente guardado** en `app/config/providers/yaml.py:51-52` (`try: import yaml  # noqa: F401` como sonda de disponibilidad), y `architecture.toml:612-615` lo declara proveedor opcional siendo TOML el formato oficial. **La decisión es correcta; lo que falta es declararlo como extra opcional en `pyproject.toml`.**

`[HECHO]` Instalado en el venv pero ausente de `pyproject.toml`: `pre-commit` 4.6.1 figura en el extra `dev`, pero **no existe `.pre-claude-config.yaml`** — corrección: no existe `.pre-commit-config.yaml`, así que el hook nunca se instala.

### 4.2 Incoherencias de versión `[HECHO]`

| Declaración | Valor | Ubicación |
|---|---|---|
| `requires-python` | `>=3.11` | `pyproject.toml:25` |
| `[tool.ruff].target-version` | `py311` | `pyproject.toml:319` |
| `[tool.mypy].python_version` | `3.13` | `pyproject.toml:256` — **modificado sin commitear** |
| Intérprete del venv | 3.13.5 | `.venv/Scripts/python.exe` |

El cambio a `3.13` está en el árbol de trabajo sin commitear. Con `requires-python = ">=3.11"`, mypy comprueba contra 3.13 código que se declara compatible con 3.11: sintaxis y comportamientos válidos en 3.13 pero no en 3.11 pasarían el gate.

### 4.3 R

**No aplica.** Cero paquetes, cero `library()`, cero `source()`, cero scripts.

---

## 5. Código muerto

**No he eliminado nada. Solo documento.**

### 5.1 Paquetes vacíos `[HECHO]`

**13 de 22 paquetes declarados no contienen un solo módulo con código.** 57 ficheros `__init__.py` bajo `app/` son solo docstring+imports.

| Bounded context | Paquetes vacíos | Estado en `delivery.toml` |
|---|---|---|
| Research | `research` + 5 subpaquetes (`data`, `features`, `signals`, `strategies`, `backtest`) | `not_started`, fase 4 |
| Discovery | `discovery` + 11 subpaquetes, `optimization` | `not_started`, fase 4 |
| Validation | `validation`, `walkforward` | `not_started`, fase 4 |
| Risk | `portfolio` | `not_started`, fase 4 |
| Execution | `execution`, `broker`, `paper`, `live` | `not_started`, fase 4 |
| Analytics | `analytics` | `not_started`, fase 4 |
| Governance | `promotion` + 5 subpaquetes, `storage/zoo` | `in_progress` (Governance) |
| Orchestration | `application` + 4 subpaquetes | `in_progress` (Orchestration) |
| Interface | `interfaces/api` | `in_progress` (Interface) |
| Foundation | `domain/services` | `in_progress` (Foundation) |

**Valoración justa:** ninguno de estos paquetes está marcado `ready`, así que **no hay contradicción de estado**. Son andamiaje declarado para fases futuras, que es exactamente lo que `architecture.toml` pretende (matriz congelada antes del código). El coste no es mentira sino **vacuidad de verificación** (§9 y §7, A-4).

### 5.2 Excepciones definidas y nunca lanzadas `[HECHO]`

`app/core/exceptions.py` define 24 clases. Conteo exacto de `raise <Nombre>` en `app/`:

| Se lanzan (9) | veces |
|---|---|
| `InvariantViolation` | 64 |
| `ConfigValidationError` | 6 |
| `ConfigError` | 5 |
| `ConfigNotFound`, `DataIntegrityError`, `StorageError` | 3 cada una |
| `InsufficientHistory` | 2 |
| `ProviderUnavailable` | 1 |

**15 nunca se lanzan.** De ellas, **13 no tienen ninguna referencia fuera de su propia definición**: `ValidationError`, `DomainError`, `DataError`, `DataGapError`, `DataSourceError`, `ProfileCycle`, `RiskViolation`, `RiskLimitBreached`, `MarginInsufficient`, `ExecutionError`, `BrokerError`, `OrderRejected`, `LookaheadDetected`.

Las otras 2 (`PlatformError` 12 refs, `MethodologyError` 1 ref, `NotValidated` 3 refs) son bases de jerarquía o se usan como tipo. `DataGapError` y `LookaheadDetected` son precisamente los ejemplos que cita `CLAUDE.md §4.3`: existen y esperan a la fase 4.

### 5.3 Módulos sin importador de primer plano `[HEUR → resuelto a HECHO]`

El análisis AST marcó 52 módulos. Los he resuelto uno a uno:

| Grupo | Módulos | Veredicto real |
|---|---|---|
| `app/interfaces/cli/*_cmd.py` (5) | `doctor_cmd`, `preflight_cmd`, `status_cmd`, `config_cmd`, `plugins_cmd` | **FALSO POSITIVO.** Se cargan desde `cli/__init__.py`. Verificado ejecutando `qp doctor/status/config/plugins/preflight`: los 5 responden. |
| `app/config/providers/yaml.py` | proveedor YAML opcional | **Vivo por diseño, sin registrar.** No aparece en la lista de proveedores de `bootstrap.py:166-174`, así que hoy no participa en ninguna resolución. |
| `app/core/math.py` | `EPSILON` + utilidades numéricas | **Sin consumidor.** Espera a los motores. |
| `app/domain/value_objects/money.py` (`Money`, `Ratio`), `time_range.py` (`TimeRange`, `Fold`) | | **Sin consumidor.** `Fold` es la unidad de walk-forward, que está vacío. |
| `app/domain/services/`, `app/storage/zoo/`, `app/interfaces/api/` | | Paquetes marcadores vacíos. |
| 44 restantes | motores y subpaquetes vacíos | Vacíos por fase. |

### 5.4 Símbolos públicos sin referencia externa `[HEUR]`

95 candidatos del análisis AST. **Marco esta cifra explícitamente como heurística**: no la he verificado símbolo a símbolo. Las que sí verifiqué (los 22 puertos, las 24 excepciones, los módulos CLI) arrojaron un falso positivo relevante (los `*_cmd`), así que la cifra bruta debe tomarse como cota superior, no como dato.

---

## 6. Componentes sin consumidores

Esta es la sección más importante del informe.

### 6.1 Tabla de puertos — `app/shared/ports.py`, 22 Protocol `[HECHO]`

| Puerto | Adaptador en el repo | Registrado en contenedor | Resuelto en producción | Clasificación |
|---|---|---|---|---|
| `ClockPort` | `SystemClock`, `FrozenClock` (`bootstrap.py:84,97`) | ✅ `bootstrap.py:212` | ✅ | **OPERATIVO** |
| `EventSinkPort` | `StructlogEventSink` (`monitoring/sink.py`) | ❌ | ❌ | **ADAPTADOR SIN REGISTRO** |
| `RunContextPort` | `RunContext` (`monitoring/run_context.py`) | ❌ | ❌ | **ADAPTADOR SIN REGISTRO** |
| `LifecyclePort` | `RuntimeMetrics` (`monitoring/runtime_metrics.py`) | ❌ | ❌ | **ADAPTADOR SIN REGISTRO** |
| `ArtifactStorePort` | `FileArtifactStore` (`storage/artifacts/store.py`) | ❌ | ❌ | **ADAPTADOR SIN REGISTRO** |
| `InstrumentCatalogPort` | `app/config/instruments.py` | ❌ | ❌ | **ADAPTADOR SIN REGISTRO** |
| `MarketDataPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `LiveFeedPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `FeatureFn` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `SignalBlockFn` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `RiskPolicyPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `BrokerPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `ExecutionSimulatorPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `TradeRepositoryPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `DatasetRepositoryPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `ConfigurationRepositoryPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `PromotionRepositoryPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `StrategyRepositoryPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `BacktestEnginePort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `StatisticalTestPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `PromotionPolicyPort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |
| `SearchSpacePort` | — | ❌ | ❌ | PUERTO SIN ADAPTADOR |

**Resumen: 1 operativo · 5 adaptadores sin registro · 16 puertos sin adaptador.**

Evidencia del «sin registro»: `grep` de `ArtifactStore|StructlogEventSink|RuntimeMetrics|InstrumentCatalog|RunContext` en `app/` **solo devuelve menciones en docstrings** (`monitoring/__init__.py:15-17`, `storage/artifacts/__init__.py:9`, `storage/__init__.py:7`). Ningún import de producción. `delivery.toml:373-376` lo admite: *«`build_platform` no los conoce, así que la plataforma sigue arrancando sin observabilidad… un adaptador que nadie inyecta es un adaptador que en producción no existe»*.

### 6.2 Bus de eventos — declarado con suscriptor, **sin publicador** `[HECHO]`

- `app/events/event.py` define **solo `Event` y `EventMeta`**. **No existe ningún catálogo de tipos de evento.** Los eventos son cadenas libres (`Event.name`).
- **El único `bus.publish(...)` en todo `app/` está en `app/events/recorder.py:143`**, dentro de la función `replay()` — es decir, reproduce eventos ya grabados.
- **Ningún componente de producción publica un solo evento.**
- Suscriptores: `Recorder` se engancha de verdad en `bootstrap.py:254` (`Recorder().attach(bus)`); `RuntimeMetrics` solo se engancha en su propio módulo y en tests.

**Consecuencia:** el bus se compone, el grabador se engancha, y no grabará nada porque nadie emite. `FeaturesCalculated` —el evento que `architecture.toml:173` y `app/events/__init__.py:4` usan para justificar la existencia del paquete— **no existe en el código.** `BUILD.md:105` exige *«el bus se prueba con los eventos de configuración»*: no hay eventos de configuración en producción.

### 6.3 Registries — registrados vacíos `[HECHO]`

Los 3 catálogos (`feature`, `signal`, `strategy`) se registran en `bootstrap.py:226-231` como `Registry(kind)` vacío. **No existe un solo uso de los decoradores de registro** (`@feature`, `@signal`, `@strategy`) en `app/`. Confirmado ejecutando `qp plugins list`:

```
Catalogos internos
  - app.research.features      (paquete vacío)
  - app.research.signals       (paquete vacío)
  - app.research.strategies    (paquete vacío)
Plugins instalados (0)
```

### 6.4 Contratos declarativos sin lector `[HECHO]`

| Contrato | Líneas | Quién lo lee | Aporta a la config efectiva |
|---|---|---|---|
| `configs/global.toml` | 54 | `bootstrap.py:167` | ✅ 2 claves |
| `configs/profiles/ci.toml` | — | `bootstrap.py:168` (solo modo `ci`) | ✅ 1 clave, solo en `ci` |
| `configs/architecture.toml` | 629 | `preflight.py:147`, `generate_docs.py:65`, `consolidate_architecture.py:480`, `test_architecture.py:30` | ❌ (no es config) |
| `configs/delivery.toml` | 411 | `bootstrap.py:319`, `doctor_cmd.py:39` | ❌ (no es config) |
| `configs/plugins.toml` | 213 | `plugins.py:73`, `preflight.py:257` | ❌ |
| **`configs/runtime.toml`** | **214** | `preflight.py:148` (modos y paquetes), `generate_docs.py:66` | **❌ 0 claves** |
| **`configs/conventions.toml`** | **226** | **solo `preflight.py:39` para comprobar que existe**, y `tests/test_conventions.py` | **❌** |

`runtime.toml` declara 6 modos con ~25 flags `allow_*` / `require_*`, una matriz `[allowed_packages]` y `[allowed_lifecycle_states]`. **La única clave que el código lee de verdad es `runtime.frozen_clock_ns`** (`bootstrap.py:239`), y viene de `profiles/ci.toml`, no de `runtime.toml`. Ningún flag (`allow_network`, `allow_broker_orders`, `allow_parallelism`, `require_seed`, `require_kill_switch`…) tiene mecanismo de aplicación en runtime.

Esto es **esperable** mientras no exista broker ni ejecución, pero significa que la afirmación de `runtime.toml:167-168` —*«Es la versión ejecutable de "el investigador no puede enviar órdenes por accidente"»*— hoy no es ejecutable.

---

## 7. Contradicciones arquitectónicas

### CRÍTICO

**C-1 · `ci_green` bloquea toda promoción y no existe CI** `[HECHO]`

`configs/delivery.toml:155-159` declara el criterio `ci_green` con `mandatory = true`, `blocking = true`, `verified_by = "pipeline completo"`. `delivery.toml:36-38`: *«No hay cumplimiento parcial: `in_progress` con doce de catorce sigue siendo `in_progress`»*.

Verificación de existencia de pipeline:
```
git ls-files | grep -iE '\.github|workflow|\.gitlab|azure|jenkins|pre-commit|tox|noxfile|Makefile'  →  NINGUNO
ls -a .github .pre-commit-config.yaml Makefile  →  No such file or directory (los tres)
```

Estado real de las 11 capacidades: **0 `ready`, 5 `in_progress` (Foundation, Orchestration, Governance, Monitoring, Interface), 6 `not_started`, 0 `blocked`.** Las 5 en curso citan `ci_green` como pendiente bloqueante.

Además `configs/architecture.toml:11` declara que el contrato gobierna *«la puerta de CI»*. **Esa puerta no existe.** Toda la gobernanza —1.821 tests, validador AST, esquema de ADR— rompe únicamente el build local de quien la ejecuta. `BUILD.md:226` lista «un test de contrato desactivado» como señal de degradación; el caso real es más fuerte: no hay build remoto que romper.

*Nota de justicia:* `delivery.toml:242-244` lo declara abiertamente (*«No existe pipeline; toda la evidencia es local… Sin CI, "está verde" es una afirmación sobre la máquina de quien la hizo»*). No es un problema oculto. Es un bloqueo conocido y sin dueño, que es exactamente lo que `BUILD.md:231` marca como señal de degradación.

**Impacto:** la Fase 4 no puede abrirse. Es el único hallazgo que bloquea el proyecto entero.

---

**C-2 · `qp doctor` —criterio de aceptación de la Fase 3— revienta en Windows** `[HECHO]`

`BUILD.md:109-113` fija como aceptación de la Fase 3:
```
git clone … && pip install -e . && qp doctor  →  Platform READY
```

Ejecutado en la consola por defecto de este equipo (Windows 11, cp1252):

```
$ qp doctor
Traceback (most recent call last):
  File ".../app/interfaces/cli/doctor_cmd.py", line 114, in main
    out.write("\n".join(_render_preflight(result)) + "\n")
  File "C:\Python313\Lib\encodings\cp1252.py", line 19, in encode
UnicodeEncodeError: 'charmap' codec can't encode character '\u2713' in position 2
EXIT=1
```

Causa raíz: `app/interfaces/cli/doctor_cmd.py:34` → `OK, FAIL, WARN, SKIP = "✓", "✗", "!", "·"`. Mismo patrón en `preflight_cmd.py:41` y `plugins_cmd.py:47`.

Matriz de códigos de salida medida:

| Comando | consola por defecto | con `PYTHONIOENCODING=utf-8` |
|---|---|---|
| `qp doctor` | **1 (traceback)** | **0 → `Platform READY`** |
| `qp preflight` | **1 (traceback)** | **0** |
| `qp status` | 1 | 1 *(por diseño, ver M-6)* |
| `qp doctor/status/preflight --json` | 0 | 0 |
| `qp config show`, `config validate`, `plugins list`, `plugins validate` | 0 | 0 |

**La plataforma funciona.** El defecto es de codificación de salida, y contradice directamente `delivery.toml:405-406`: *«Todos con `--json`, todos con código de salida distinguido (0 correcto, 1 fallo del comando, 2 uso incorrecto)»*. Aquí el 1 no es «fallo del comando»: es un traceback no capturado en el gate que cierra la fase.

---

**C-3 · Un artefacto derivado con reloj de pared, verificado byte a byte** `[HECHO]` — **este exige tu decisión**

- `scripts/generate_docs.py:232`:
  ```python
  f"\n---\n\nGenerado el {datetime.now(UTC).strftime('%Y-%m-%d')} por "
  ```
- `tests/test_governance.py:473-495` (`test_derived_docs_are_in_sync`) compara `docs/ARCHITECTURE.md` **byte a byte** con lo que produciría el generador.
- `configs/delivery.toml:104-108` declara `docs_regenerated` con `blocking = true`, verificado por ese test.

**Es la única falla de los 1.821 tests ahora mismo:**
```
FAILED tests/test_governance.py::test_derived_docs_are_in_sync
E   - l 2026-07-30 por `scripts/generate_docs.py`.
E   + l 2026-07-29 por `scripts/generate_docs.py`.
```
(Hora local del sistema: 2026-07-29 22:13; UTC ya es el día 30 — el fallo aparece cada noche a partir de las 22:00 locales, además de cada día siguiente a la última regeneración.)

**Por qué es estructural y no cosmético:** el mecanismo que implementa **P7** (*todo artefacto derivado se regenera automáticamente*) inyecta un reloj de pared, que es exactamente lo que **P6** prohíbe (*sin reloj de pared*) y lo que **P1** exige eliminar para que dos ejecuciones coincidan. El generador no puede producir salida reproducible, y el test que lo vigila no puede pasar de forma estable. Un criterio bloqueante que se pone rojo solo por el paso del tiempo es un criterio que acabará desactivado — el escenario que `BUILD.md:226` nombra como muerte de la gobernanza.

Agravante: `scripts/consolidate_architecture.py:724` estampa además **hora, minuto y segundo** UTC en `reports/architecture_report.md`.

**No he modificado nada.** Alternativas, para que decidas:

| Opción | Coste | Consecuencia |
|---|---|---|
| **A.** Quitar la fecha del artefacto y registrarla aparte (p.ej. en un `.meta` no verificado) | 1 línea en el generador + regenerar | P7 y P1 dejan de contradecirse. Se pierde el sello visible en el documento. |
| **B.** Derivar la fecha del contrato en lugar del reloj (fecha del último commit que tocó `configs/`) | ~5 líneas | Determinista y con significado real: «generado desde esta versión de los contratos». Introduce dependencia de git en el generador. |
| **C.** Normalizar la fecha en el test antes de comparar | ~3 líneas en el test | Más barato, pero debilita el test y deja el reloj de pared dentro del generador. Contradice P10 (se ajusta la verificación, no el código). |
| **D.** Aceptar el fallo diario | 0 | Inaceptable: `docs_regenerated` es bloqueante. |

**Recomiendo B**, y en su defecto A. **C** invierte P10 y no la propondría sin que lo pidas explícitamente. Requiere ADR: modifica el comportamiento de un artefacto derivado declarado en `architecture.toml:44-51`.

---

### ALTO

**A-1 · 5 adaptadores construidos, probados y no inyectados.** Ver §6.1. `delivery.toml` lo reconoce para Monitoring; no lo reconoce para `FileArtifactStore` ni `InstrumentCatalog`. `[HECHO]`

**A-2 · Bus sin publicador.** Ver §6.2. `BUILD.md:105-106` exige un consumidor real por bloque de la Fase 3 (*«Un bloque sin consumidor es especulación y se recorta»*). El bus tiene suscriptor y no tiene emisor. `[HECHO]`

**A-3 · La matriz de modos no gobierna en la dirección que importa** `[HECHO]`

`configs/runtime.toml:171-192` declara `[allowed_packages]`. Para `research`: 14 paquetes, **sin `container`, `config` ni `events`** — los tres imprescindibles para que `build_platform` componga la plataforma en ese modo. Para `ci`: 7 paquetes, sin `container`, `config`, `events` ni `interfaces`, que es precisamente lo que la suite ejercita.

`app/container/preflight.py:213-226` (`_check_mode_packages_exist`) comprueba **solo** que todo paquete *autorizado* esté *declarado en la matriz*. **No comprueba que los paquetes usados estén autorizados.** La dirección que impediría «enviar órdenes por accidente» no está implementada, y el desajuste actual pasa desapercibido.

**A-4 · 315 de 545 instancias de test de matriz no ejecutan su cuerpo** `[HECHO]`

`tests/test_architecture.py` parametriza 5 tests sobre los 109 módulos de `app/`: `test_matrix_is_respected`, `test_no_relative_imports`, `test_no_legacy_import_paths`, `test_third_party_confined`, `test_no_wall_clock`. Todos iteran sobre los imports del módulo.

Medición directa (script AST propio): **63 de los 109 módulos no contienen ningún import de `app.*`.** Para ellos el bucle no entra y el test pasa sin afirmar nada. 5 × 63 = **315 instancias vacuas de 545 (58 %)**.

La matriz de dependencias —el corazón declarado de la arquitectura— se aplica de hecho sobre **46 módulos**. Las aristas prohibidas entre motores (Feature→Broker, Risk→Indicators, Discovery→Promotion) se verifican sobre paquetes vacíos: ningún fichero puede violarlas porque no hay ficheros.

Esto **no invalida** el mecanismo: el validador AST es correcto y entrará en vigor solo con que aparezca el primer módulo de motor. Pero convierte «1.821 tests verdes» en una señal más débil de lo que parece.

**A-5 · 2 de las 8 invariantes globales de SYSTEM_MODEL no tienen mecanismo posible** `[HECHO]`

`SYSTEM_MODEL.md:238-256` lista 8 dependencias prohibidas y afirma: *«Todas están o estarán verificadas por `tests/test_architecture.py` contra la matriz de dependencias»*. Verificación una por una:

| Invariante | Arista en `architecture.toml` | Mecanismo |
|---|---|---|
| Feature → Broker | ✅ `research.depends` no incluye `broker` | ✅ pero vacuo (A-4) |
| Signal → MT5 | ✅ `rules.third_party.research` prohíbe `MetaTrader5` | ✅ pero vacuo |
| Risk → Indicators | ✅ `portfolio.depends` no incluye `research` | ✅ pero vacuo |
| Portfolio → Análisis técnico | ✅ igual que la anterior | ✅ pero vacuo |
| **Execution → Decisión** | ❌ «decisión» no es un paquete | **❌ sin mecanismo** |
| **Validation → Estrategia (escritura)** | ❌ `validation.depends` **sí incluye** `research`; la prohibición es sobre *escribir*, y ninguna arista expresa dirección de escritura | **❌ sin mecanismo** |
| Discovery → Promotion | ✅ `discovery.depends` no incluye `promotion` | ✅ pero vacuo |
| Cualquiera → Reloj de pared | ⚠️ `rules.wall_clock.forbidden_in_layers = [core, domain, shared, engine]` — **4 de 8 capas** | ⚠️ alcance reducido |

Las dos sin mecanismo son las dos que protegen el núcleo científico: que la ejecución no decida y que la validación no reescriba la estrategia. Son las que convierten out-of-sample en in-sample si se rompen, y hoy dependen solo de revisión humana.

**A-6 · Dos criterios distintos de cierre para la Fase 3** `[HECHO]`

- `BUILD.md:109-113`: cierre = `qp doctor → Platform READY`.
- `BUILD.md:186-195` + `delivery.toml`: cierre = todas las capacidades `ready` + criterios `blocking` + `qp doctor` verde + ADR de cierre.

Con `PYTHONIOENCODING=utf-8`, `qp doctor` **ya imprime `Platform READY` y sale 0** — mientras 0 de 11 capacidades están `ready` y falta el ADR de cierre (`delivery.toml:408`). Los dos documentos que gobiernan el mismo hecho dan respuestas opuestas. Es el patrón que **P5** existe para evitar.

**A-7 · Referencias colgantes en los contratos** `[HECHO]` — comprobé las 46 rutas citadas en `configs/` y `decisions/`; 4 no existen, más 1 en prosa punteada:

| Referencia inexistente | Citada en |
|---|---|
| `tests/test_no_lookahead.py` | `configs/architecture.toml:524`, `decisions/ADR-0002.toml:52`, `docs/ARCHITECTURE.md:249`, **`app/shared/ports.py:120`** |
| `reports/freeze_findings.md` | `decisions/ADR-0007.toml:40,80` (**es su `validation.reference`**), `BUILD.md:151` |
| `reports/platform_state.toml` | `configs/delivery.toml:186` |
| `app/core/registry.py` | `decisions/ADR-0003.toml:83`, `scripts/consolidate_architecture.py:202` |
| `core.config.models` (módulo punteado) | `configs/architecture.toml:600` |

El caso más grave es `tests/test_no_lookahead.py`: `app/shared/ports.py:120` promete al implementador de `FeatureFn` que su contrato de causalidad *«está verificado por `tests/test_no_lookahead.py`»*, y `architecture.toml:521-525` sostiene el flag `causal_features_only` sobre ese mismo test. **La garantía anti-look-ahead —la propiedad que hace válido un backtest— no tiene verificación alguna.** Las últimas dos son referencias históricas en un ADR y en el consolidador, aceptables.

**A-8 · Dos ADR aceptados con validación que no resuelve, y el test que debería detectarlo no lo hace** `[HECHO]`

Los 8 ADR son conformes al esquema (`test_decision_matches_schema` pasa). Pero comprobando si sus `validation.reference` existen:

| ADR | reference | ¿Existe? |
|---|---|---|
| ADR-0001, 0002, 0003, 0005, 0008 | nodos de `tests/test_architecture.py` | ✅ |
| ADR-0004 | `tests/test_governance.py::test_every_principle_is_invoked` | ✅ |
| **ADR-0006** | `tests/test_governance.py::test_every_package_is_governed` | **❌ NO EXISTE** (el test está en `test_architecture.py`) |
| **ADR-0007** | `reports/freeze_findings.md` | **❌ NO EXISTE** |

`tests/test_governance.py:354-355`:
```python
if adr_schema().get("validation", {}).get("require_reference", True):
    assert validation.get("reference"), f"{stem}: la validacion no apunta a nada"
```
**Solo comprueba que la cadena no esté vacía.** `decisions/schema.toml` justifica `require_reference` diciendo que *«"se revisa en code review" sin decir qué se busca no es una validación, es una intención»* — y el test admite un puntero a la nada, que es el mismo problema con otra forma.

**A-9 · `conventions.toml` no cita ningún principio, y es donde se reduce P6** `[HECHO]`

Citas de principios por contrato: `architecture.toml` 4, `delivery.toml` 6, `plugins.toml` 2, `runtime.toml` 1, **`conventions.toml` 0**, `global.toml` 0, `profiles/ci.toml` 0.

`CONSTITUTION.md:23-27` describe el mecanismo: *«Los contratos de `configs/` los citan en sus tablas `principle`… Un principio que ningún contrato invoca es retórica»*. El test verifica la dirección «todo principio citado», no «todo contrato cita». Y `conventions.toml` —226 líneas, el contrato que gobierna determinismo y convenciones— es precisamente donde se **reduce el alcance de P6**:

- `CONSTITUTION.md:84-86` (P6): *«Sin variables de módulo mutables. Sin generadores de números aleatorios globales. Sin reloj de pared en las capas puras.»* Redacción absoluta para las dos primeras.
- `configs/conventions.toml:129`: `forbid_global_mutable_state = ["core", "domain", "shared", "engine"]` — **4 de 8 capas**.

Consecuencia concreta: `app/container/bootstrap.py:279` → `_REGISTRY_PORTS: dict[str, type] = {}`, un **diccionario mutable de módulo** que se escribe en `:295`. Está fuera del alcance del contrato (capa `application`) y por tanto **ningún test lo detecta**, pero P6 lo prohíbe sin matices. Funcionalmente es una memoización idempotente y benigna; formalmente es una relajación de un principio constitucional hecha en el único contrato que no cita principios, y sin ADR que la registre (`P3`, `P9`).

---

### MEDIO

**M-1 · `CLAUDE.md` contradice a `architecture.toml` sobre pydantic** `[HECHO]`
`CLAUDE.md §2` fija pydantic/pydantic-settings como stack de configuración y tipado, y `§4.1` prohíbe dicts genéricos exigiendo «modelos de Pydantic o `@dataclass`». `configs/architecture.toml:581-592` **prohíbe pydantic en `core`, `domain`, `shared` y en los 8 motores**, con 17 líneas de justificación (`:595-611`). La realidad implementada: `dataclasses` congeladas + `tomllib`, cero pydantic. **El código sigue al contrato, que es lo correcto por P10 — es `CLAUDE.md` el que está desalineado**, y arrastra a `pyproject.toml`, que declara pydantic como dependencia base.

**M-2 · `CLAUDE.md` manda typer; la CLI es argparse** `[HECHO]` `pyproject.toml:97` declara `typer>=0.15` (0.27.0 instalado) y `CLAUDE.md §3.4` dice «CLI vía `typer`». `app/interfaces/cli/` usa `argparse` y `sys.stdout.write`. Funciona bien y sin dependencia; la decisión no está registrada en ningún ADR (P3).

**M-3 · `mypy --strict` no cubre `scripts/` ni `tests/`** `[HECHO]`
```
mypy app                        → Success: no issues found in 109 source files
mypy app scripts tests main.py  → Found 19 errors in 4 files (checked 128 source files)
```
Desglose: `scripts/consolidate_architecture.py` 6 (incluido un `# type: ignore[union-attr]` que mypy declara **inútil** y que además oculta un `attr-defined` real en `:637`), `tests/test_events.py` 6, `tests/test_monitoring.py` 4, `tests/test_container.py` 3.

`CLAUDE.md §2` exige «mypy (modo `--strict` absoluto)»; `delivery.toml:131-135` declara `no_warnings` verificado por «ruff + mypy --strict» con `blocking = true`, sin decir sobre qué corpus. `delivery.toml:233-234` acota honestamente a *«`ruff check` y `mypy --strict` están limpios sobre `app/`»*. **No hay configuración que excluya `scripts/` y `tests/`: simplemente nadie los comprueba**, porque no hay CI (C-1) ni pre-commit.

**M-4 · `readme = "README.md"` y README.md no existe** `[HECHO]` `pyproject.toml:23`. `ls README.md` → `No such file or directory`. El único README del repo es `plugins/README.md`. `[HEUR]` No he ejecutado el backend de construcción para no escribir en el repo, así que **no afirmo que el `pip wheel` falle**; sí afirmo que la referencia está colgada. `BUILD.md:110` incluye `pip install -e .` en el criterio de aceptación de la Fase 3.

**M-5 · `qp doctor` corre menos comprobaciones que `qp preflight`** `[HECHO]`

```
qp doctor --json     → checks_run: 7   (sin config_frozen)
qp preflight --json  → checks_run: 8   (con config_frozen)
```
`app/interfaces/cli/doctor_cmd.py:92` llama `Preflight(ROOT, mode=args.mode).run()` sin `effective_config`, y `preflight.py:128-129` solo ejecuta `_check_config_is_frozen` cuando ese argumento llega. El gate que `BUILD.md` usa para cerrar la Fase 3 es el más débil de los dos.

Lo notable: `preflight.py:52-55` documenta exactamente este riesgo — *«un arranque que pasó porque una comprobación no se ejecutó es indistinguible de uno que pasó de verdad, salvo que se registre la lista»*. El diseño anticipó el problema, registró la lista, y el caso ocurrió igual.

**M-6 · `qp status` da 0 en `--json` y 1 en texto para el mismo estado** `[HECHO]` `status_cmd.py:56` devuelve `0 if report.get("composed")`; `:90` devuelve `1` si falta alguna capacidad para Discovery. El estado del sistema es idéntico; el código de salida depende del formato de salida. Contradice `delivery.toml:405-406`.

**M-7 · Regla de negocio hardcodeada en la CLI, con comentario que lo admite** `[HECHO]` `app/interfaces/cli/status_cmd.py:30-40`:
```python
#: Capacidades que deben estar `ready` antes de abrir Discovery. Se declaran
#: aqui y no en un documento porque es la condicion que el sistema comprueba.
_REQUIRED_FOR_DISCOVERY: tuple[str, ...] = (
    "Foundation", "Orchestration", "Governance", "Research", "Risk", "Execution", "Analytics",
)
```
Contradice **P4** (*«Una restricción existe primero como contrato legible y verificable, y solo después como código»*) y `CLAUDE.md §4.5`. El comentario invierte explícitamente la jerarquía de gobierno. Además duplica el `phase_rule` de `delivery.toml:23-31`, que dice «TODAS las capacidades de plataforma» mientras el código enumera 7 concretas: dos fuentes para el mismo hecho (P5).

**M-8 · Umbral de negocio en el dominio sin razón del número** `[HECHO]` `app/domain/value_objects/metrics.py:24` → `MIN_TRADES_FOR_INFERENCE: int = 30`, usado en `:116`. El comentario explica **qué** significa, no **por qué 30**. `CONSTITUTION.md:112` (P9): *«Ningún umbral existe sin la razón por la que es ese número.»* Además está hardcodeado en lugar de en configuración (`CLAUDE.md §4.5`), y sin `Final`.

**M-9 · 2 de los 3 artefactos derivados declarados están desfasados y sin verificación** `[HECHO]`

`architecture.toml:44-51` declara tres artefactos derivados: `docs/ARCHITECTURE.md`, `reports/architecture_report.md`, `reports/dependency_graph.svg`. **`test_derived_docs_are_in_sync` solo comprueba el primero.**

Los `reports/` están sellados el **28-jul 06:44**, cuatro commits atrás (último commit: 28-jul 18:37). Prueba del desfase por contenido: `reports/duplicated_symbols.txt` dice
```
main: app.interfaces.cli, app.interfaces.cli.doctor, app.interfaces.cli.status
```
y `app/interfaces/cli/doctor.py` **se borró en el commit `4f4c6a3`** (verificado con `git log --diff-filter=D`); hoy el módulo se llama `doctor_cmd.py`. `BUILD.md:228` clasifica «documentación editada a mano» como señal de degradación; el caso real es documentación derivada **no regenerada** y no vigilada.

**M-10 · `docs/ARCHITECTURE.md` modificado a mano en el árbol de trabajo** `[HECHO]` `git diff` muestra un único cambio: `2026-07-28` → `2026-07-29` en la línea del sello. Es una edición manual de un artefacto que `architecture.toml:52-56` declara que *«no se edita nunca más»* — y es la evidencia directa de que C-3 ya está obligando a tocar a mano el fichero para callar el test.

**M-11 · La CLI asume layout de código fuente** `[HECHO]` `ROOT = Path(__file__).resolve().parents[3]` en los 5 módulos `*_cmd.py`. Instalada como wheel en `site-packages`, `parents[3]` no apunta a la raíz del proyecto y no hay `configs/` allí. `BUILD.md:110` exige que `pip install -e . && qp doctor` funcione: con `-e` funciona, en instalación normal no.

**M-12 · `BUILD.md` «Estado actual» desfasado** `[HECHO]` `BUILD.md:235-246` afirma tres cosas hoy falsas:

| Afirmación de `BUILD.md` | Realidad verificada |
|---|---|
| «Fase 2.5 sin ejecutar» | `git tag -l` → **`architecture-baseline` existe** |
| «coexisten dos jerarquías (`core/` y `app/core/`, `domain/` y `app/domain/`)» | El propio consolidador informa `skip core (ausente)` / `skip domain (ausente)` |
| «la capacidad `Foundation` está en `blocked`» | `delivery.toml:200` → `status = "in_progress"` |
| «ninguna prueba se ha lanzado» | 1.821 tests colectados, 1.820 pasan |

`delivery.toml` está actualizado y es fiable; `BUILD.md` se quedó atrás. Dado que ambos son documentos de gobierno, un agente que retome el proyecto leyendo `BUILD.md` primero —que es lo que el propio documento pide— partirá de un estado falso.

**M-13 · 211 usos de `Any` en `app/`** `[HECHO]` Concentrados en `shared/ports.py` (21), `container/preflight.py` (14), `core/config/provenance.py` (13), `monitoring/sink.py` (12), `container/bootstrap.py` (12). `CLAUDE.md §4.1`: «No uses `Any`». Buena parte es legítima (varianza de `Protocol`, cargas genéricas de evento, `payload` de TOML), pero es una prohibición absoluta con 211 excepciones no registradas (P9).

**M-14 · `UP038` ignorada sin efecto** `[HECHO]` `ruff check .` emite: *«The following rules have been removed and ignoring them has no effect: UP038»*. El comentario de `pyproject.toml:365-373` razona correctamente por qué se rechaza la regla y menciona que ruff la retiró en 0.12 — con ruff 0.16 la entrada ya es inerte. Es el único warning de la herramienta y contradice `no_warnings`.

---

### BAJO

**B-1 · Objetos fundamentales de `SYSTEM_MODEL.md §2` sin implementación** `[HECHO]` Inventario del dominio: 19 clases. Cumplen su contrato `Bars` (invariantes + buffers no escribibles), `SignalOutput` + `ReasonCode`, `Position` (con MAE/MFE en `trade.py:59-60`), `Trade` (`gross_pnl`, `net_pnl` como propiedad, `exit_reason`, `mae`, `mfe`).

No existen como tipo: **`Feature`/`FeatureVector`** (solo el `Protocol FeatureFn`) y **`Dataset`** (solo `DatasetRepositoryPort`). `Order` cubre 2 de los «tres momentos» exigidos (`OrderIntent` + `Order` con `filled_lots`/`average_price`); no hay tipo `Settlement` separado. Esperable en fase 3, pero son objetos declarados normativos.

**B-2 · El dominio no tiene test propio** `[HECHO]` Los 11 ficheros de test son `architecture`, `config_resolution`, `container`, `conventions`, `determinism`, `events`, `governance`, `instruments`, `monitoring`, `plugins`, `storage`. **No hay `test_domain`, `test_bars`, `test_order` ni `test_trade`.** `app/domain/` (9 módulos, ~1.500 LOC con las invariantes de `Bars` y la máquina de estados de `Order`) se ejercita solo indirectamente vía `test_conventions` (estructural) y como tipo en otros tests.

**B-3 · Supresiones** `[HECHO]` En `app/`, solo 2 y ambas justificadas en línea: `config/providers/yaml.py:52` (`# noqa: F401`, sonda de import) y `domain/entities/bars.py:178` (`# noqa: A002`, `open` es nombre del dominio OHLC). En `tests/` 6 y en `scripts/` 2, una de ellas inútil (M-3). El proyecto cumple `no_suppressions` en `app/`.

**B-4 · TODO/FIXME** `[HECHO]` 2 aciertos en `app/`, ambos **falsos positivos** (la palabra «TODOS» en prosa castellana: `container.py:407`, `preflight.py:16`). No hay deuda marcada.

---

## 8. Riesgos de integración

Sin R, los riesgos de integración son internos y de empaquetado.

| # | Riesgo | Severidad | Evidencia |
|---|---|---|---|
| I-1 | **La CLI humana no funciona en la consola por defecto del SO del proyecto.** Cualquier operador que ejecute `qp doctor` o `qp preflight` sin `PYTHONIOENCODING` ve un traceback | Crítico | C-2 |
| I-2 | **La CLI no funciona instalada como wheel** (`parents[3]` + `configs/` ausente de los datos del paquete) | Medio | M-11 |
| I-3 | **`readme = "README.md"` colgado** en los metadatos del paquete | Medio | M-4 |
| I-4 | **`PyYAML` importado sin declarar.** El proveedor YAML solo funciona si algo más lo instaló | Medio | §4.1 |
| I-5 | **`run_context.py` invoca `git` por `subprocess`.** Instalado como wheel no hay `.git`; con el repo dirty el `code_version` no identifica un estado reproducible. El manejo de error existe (`except (subprocess.SubprocessError, OSError)`, `:60,73`), así que degrada en vez de romper — pero P1 exige registrar la versión de código y en ese camino no la hay | Medio | `app/monitoring/run_context.py:52-73` |
| I-6 | **Sin CI, ningún entorno distinto del de desarrollo se ha ejercitado nunca.** I-1 e I-2 son exactamente la clase de defecto que un CI en Linux + una prueba de instalación habrían detectado el primer día | Alto | C-1 |
| I-7 | **`configs/profiles/research.toml` no existe** siendo `research` el modo por defecto. La capa de perfil está siempre ausente en el modo normal de operación | Medio | §2.5 |

---

## 9. Riesgos de determinismo

**Balance: el diseño de determinismo es de los mejores que he auditado. El fallo está en el borde, no en el núcleo.**

### Lo que está bien `[HECHO]`

- **Reloj:** una sola lectura de reloj de pared en todo `app/` — `bootstrap.py:94` (`time.time_ns()`) dentro de `SystemClock`, en la raíz de composición (capa `application`, permitida por `rules.wall_clock.forbidden_in_layers`). Todo lo demás recibe `ClockPort`. Los modos deterministas (`backtest`, `ci`, `benchmark`, `deterministic`) reciben `FrozenClock` automáticamente (`bootstrap.py:237-244`). **Cero `datetime.now`, `utcnow`, `time.time`, `perf_counter` o `monotonic` en el resto de `app/`.**
- **Aleatoriedad:** `app/core/determinism.py:148` expone `rng_for(master_seed, namespace, *parts)` que deriva un `np.random.Generator` aislado; `:130` `derive_seed`. **Cero `random`, `secrets`, `uuid4` o `np.random` global en `app/`.** La cita de `architecture.toml:504` resuelve correctamente.
- **Hashing:** `hashlib`/`sha256` (`fingerprint.py:38` `ALGORITHM: Final = "sha256"`), no `hash()` de Python. `canonical_json` (`determinism.py:45`) normaliza antes de hashear.
- **Orden:** `sorted()` presente en las rutas críticas — `fingerprint.py` 3, `resolver.py` 5, `provenance.py` 3, `determinism.py` 2, `store.py` 1 (con el único `glob`, ordenado).
- **`test_determinism.py`** relanza el intérprete en subprocesos separados para comparar huellas, lo que sí detecta dependencia de `PYTHONHASHSEED`. Pasa.
- **`environment_record()`** (`preflight.py:359-377`) deja Python/plataforma **fuera** de la huella de corrida a propósito, con 10 líneas de razonamiento correcto.

### Los riesgos reales

| # | Riesgo | Severidad | Evidencia |
|---|---|---|---|
| D-1 | **Reloj de pared en un artefacto derivado con test byte a byte.** El único fallo de la suite | Crítico | C-3 · `generate_docs.py:232`, `test_governance.py:489` |
| D-2 | **`consolidate_architecture.py:724` estampa hora-minuto-segundo UTC** en `reports/architecture_report.md`, declarado artefacto derivado. Nadie lo verifica, y por eso no falla | Alto | M-9 |
| D-3 | **La huella de configuración cubre 2 claves.** `fingerprint_stable` es cierto y trivial: `sha256:dc038bf3e62f0fd5` sobre `config.hot_reload` y `meta.version`. `runtime.toml` (214 líneas de parámetros) aporta 0 | Alto | §2.5, §6.4 |
| D-4 | **El criterio de la Fase 3.5 es vacuo hoy.** `BUILD.md:120-123` exige «doble ejecución con la misma semilla produce artefactos idénticos bit a bit». No hay motor que produzca artefactos: el criterio no puede fallar porque no hay nada que comparar | Alto | §2.3 |
| D-5 | **Sin verificación de causalidad.** `tests/test_no_lookahead.py` no existe y es lo que sostiene `causal_features_only` y el contrato de `FeatureFn` | Alto | A-7 |
| D-6 | **`_REGISTRY_PORTS` mutable de módulo**, fuera del alcance del contrato pero prohibido por P6 sin matices. Benigno (memoización idempotente) pero no gobernado ni registrado | Medio | A-9 · `bootstrap.py:279,295` |
| D-7 | **`allow_parallelism` no se aplica.** `runtime.toml:80` lo prohíbe en `deterministic` con un razonamiento exacto sobre el orden de reducción en punto flotante. Ningún código lee el flag | Medio | §6.4 |
| D-8 | **`code_version` puede quedar vacío o «dirty»** sin que nada lo delate | Medio | I-5 |

---

## 10. Riesgos de mantenimiento

| # | Riesgo | Severidad | Evidencia |
|---|---|---|---|
| Mt-1 | **La gobernanza no puede romper ningún build compartido.** Todo el aparato de verificación depende de que una persona ejecute `pytest` a mano | Crítico | C-1 |
| Mt-2 | **`BUILD.md` —el documento que el propio proyecto designa para quien retome el trabajo— describe un estado que ya no existe** en 4 afirmaciones | Alto | M-12 |
| Mt-3 | **Cinco fuentes de verdad sobre la arquitectura.** `configs/architecture.toml` (normativa), `docs/ARCHITECTURE.md` (derivada), `pyproject.toml:171-202` (comentario de 30 líneas describiendo los 18 paquetes y su propósito), `app/*/__init__.py` (docstrings por paquete), `reports/architecture_report.md` (derivada, desfasada). Solo la primera gobierna y solo la segunda se verifica. El comentario de `pyproject.toml` no se regenera ni se comprueba | Alto | P5 |
| Mt-4 | **`pyproject.toml [tool.quantplatform]`** (`:388-427`) declara 20 propiedades arquitectónicas (`execution_engine = "event-driven"`, `storage = "repository"`, `testing = "contract-first"`, `source_of_truth = "docs"`…). **Ningún código lee esa tabla** y varias son falsas hoy. `source_of_truth = "docs"` **contradice a P4** (`CONSTITUTION.md:67`: *«La arquitectura no es `app/`: es `configs/`»*) | Medio | `[HECHO]` grep de `tool.quantplatform` en `app/` y `scripts/`: 0 |
| Mt-5 | **`scripts/consolidate_architecture.py`, 802 líneas, es el fichero más grande del repo** y su trabajo ya está hecho (la Fase 2.5 se ejecutó, el tag existe, es idempotente: `skip … (ausente)` en todo). Acumula 6 de los 19 errores de mypy. Herramienta de un solo uso que ya nadie ejercita | Medio | §5, salida del script |
| Mt-6 | **Los tests de gobernanza son en buena medida TOML contra TOML.** De los 21 tests de `test_governance.py`, la mayoría comparan contratos entre sí o contra `decisions/`. Son valiosos (detectan errores humanos al editar la matriz) pero no gobiernan el código. Los que sí lo gobiernan son los 5 parametrizados de `test_architecture.py` y los 10 de `test_conventions.py`, con la limitación de A-4 | Medio | lectura completa de los 3 ficheros |
| Mt-7 | **6 dependencias declaradas sin uso** empujan a instalar 6 árboles de paquetes (scipy, pandas, pyarrow, pydantic ×2, typer) para una plataforma que solo necesita numpy, orjson, structlog | Bajo | §4.1 |
| Mt-8 | **211 `Any` y una prohibición absoluta de `Any`** garantizan que la regla se lea como orientativa | Bajo | M-13 |

---

## 11. Lista priorizada de correcciones

**No he aplicado ninguna.** Los tres primeros exigen decisión previa (§7 y §12).

### Crítico

| # | Corrección | Referencia | Requiere ADR |
|---|---|---|---|
| 1 | **Resolver el bloqueo `ci_green`.** O se crea el pipeline (y `ci_green` pasa a ser satisfacible), o se reclasifica el criterio con ADR. Mientras siga `blocking` sin pipeline, la Fase 4 es inalcanzable por construcción y el proyecto está formalmente detenido | C-1, Mt-1 | **Sí** en el segundo caso |
| 2 | **Arreglar la codificación de salida de la CLI.** `doctor_cmd.py:34`, `preflight_cmd.py:41`, `plugins_cmd.py:47`. Reconfigurar el stream a UTF-8 o degradar a ASCII cuando la consola no soporta el glifo. Es el criterio de aceptación de la Fase 3 y hoy sale 1 con traceback | C-2, I-1 | No |
| 3 | **Eliminar el reloj de pared del generador de documentación.** Cuatro alternativas evaluadas en C-3; recomiendo derivar la fecha del contrato (opción B). Afecta también a `consolidate_architecture.py:724` | C-3, D-1, D-2, M-10 | **Sí** |

### Alto

| # | Corrección | Referencia |
|---|---|---|
| 4 | **Crear `tests/test_no_lookahead.py` o retirar las 4 citas.** Hoy `app/shared/ports.py:120` promete al implementador una verificación inexistente, y es la que hace válido un backtest | A-7, D-5 |
| 5 | **Corregir las referencias de validación de ADR-0006 y ADR-0007, y endurecer `test_decision_validation_is_actionable`** para que compruebe que la referencia resuelve (nodo de pytest colectable o fichero existente) | A-8 |
| 6 | **Registrar los 5 adaptadores existentes en `build_platform`** (el bloque B2 que `delivery.toml:373` ya nombra), o marcarlos explícitamente como diferidos. Hoy la plataforma arranca sin observabilidad ni persistencia | A-1, §6.1 |
| 7 | **Dar un publicador real al bus** — los eventos de configuración que `BUILD.md:105` ya exige — y declarar un catálogo de nombres de evento. Hoy el `Recorder` no grabará nada | A-2, §6.2 |
| 8 | **Completar `[allowed_packages]`** con `container`, `config` y `events` en `research` y `ci`, e implementar la comprobación en la dirección que gobierna (paquete usado ⊆ paquete autorizado) | A-3 |
| 9 | **Actualizar «Estado actual» de `BUILD.md`** o derivarlo de `delivery.toml` para que no pueda desfasarse otra vez (P7) | M-12, Mt-2 |
| 10 | **Extender la verificación de sincronía a los 3 artefactos derivados declarados**, o retirar de `[derived].artifacts` los dos que nadie comprueba | M-9 |
| 11 | **Decidir el alcance de P6**: o `conventions.toml` deja de reducirlo a 4 capas, o la reducción se registra con ADR y `conventions.toml` gana su tabla `principle` | A-9, D-6 |
| 12 | **Unificar el criterio de cierre de la Fase 3** entre `BUILD.md:109-113` y `delivery.toml` | A-6 |
| 13 | **Ampliar el gate de mypy a `scripts/` y `tests/`** (19 errores, incluido un `type: ignore` que oculta un fallo real en `consolidate_architecture.py:637`) | M-3 |

### Medio

| # | Corrección | Referencia |
|---|---|---|
| 14 | Alinear `CLAUDE.md` con `architecture.toml` sobre pydantic, y con la realidad sobre typer. Hoy las instrucciones para agentes contradicen el contrato normativo en dos puntos del stack | M-1, M-2 |
| 15 | Limpiar `[project].dependencies`: retirar scipy/pandas/pyarrow/pydantic/pydantic-settings/typer o justificar su presencia; declarar PyYAML como extra opcional | §4.1, Mt-7 |
| 16 | Crear `README.md` o retirar `readme` de `pyproject.toml` | M-4 |
| 17 | Pasar `effective_config` a `Preflight` desde `doctor_cmd.py:92` para que el gate de aceptación corra las 8 comprobaciones | M-5 |
| 18 | Unificar el código de salida de `qp status` entre texto y `--json` | M-6 |
| 19 | Mover `_REQUIRED_FOR_DISCOVERY` de `status_cmd.py:32-40` a `delivery.toml` | M-7 |
| 20 | Llevar `MIN_TRADES_FOR_INFERENCE` a configuración, o documentar por qué es 30 y marcarlo `Final` | M-8 |
| 21 | Resolver `configs/profiles/research.toml` (crearlo, o hacer que la ausencia del perfil del modo por defecto sea explícita) | I-7 |
| 22 | Hacer que la CLI localice `configs/` sin depender de `parents[3]` | M-11, I-2 |
| 23 | Unificar la versión de Python entre `requires-python`, ruff y mypy; commitear o revertir el cambio pendiente | §4.2 |
| 24 | Regenerar `reports/` y decidir si `consolidate_architecture.py` se archiva | M-9, Mt-5 |
| 25 | Retirar el ignore de `UP038`, ya inerte | M-14 |
| 26 | Decidir qué hacer con `pyproject.toml [tool.quantplatform]` y el comentario de 30 líneas de `[tool.setuptools.packages.find]`: nadie los lee y compiten con `architecture.toml` | Mt-3, Mt-4 |

### Bajo

| # | Corrección | Referencia |
|---|---|---|
| 27 | Añadir tests directos del dominio (`Bars`, `Order`, `Trade`, `Position`) | B-2 |
| 28 | Registrar con ADR las decisiones estructurales que no lo tienen: argparse en la CLI, desactivación global de `type-abstract`, ignore de `UP038`, alcance por capas de P6 | A-9, M-2, P3 |
| 29 | Cuando se abra la Fase 4, revisar las 13 excepciones sin referencia y `app/core/math.py`, `money.py`, `time_range.py` | §5.2, §5.3 |
| 30 | Reducir el uso de `Any` en `app/shared/ports.py` o registrar la excepción (P9) | M-13 |

---

## 12. Estado de preparación

| Pregunta | Respuesta |
|---|---|
| **Arquitectura comprendida** | **Sí.** He reconstruido las 8 capas, los 22 paquetes declarados con su matriz de dependencias cerrada, las 11 capacidades con su estado real, los 22 puertos con su clasificación de consumo, la cadena de resolución de configuración, la máquina de fases del contenedor y el conjunto exacto de 6 componentes que compone `build_platform`. |
| **Contexto reconstruido** | **Sí.** Leídos íntegros: los 5 documentos de gobierno, los 7 contratos TOML, los 8 ADR con su esquema, `pyproject.toml`, y los ficheros de código que sostienen las conclusiones (`ports.py`, `bootstrap.py`, `preflight.py`, `status_cmd.py`, `runtime.toml`, `architecture.toml`, `delivery.toml`, `test_architecture.py`, `main.py`). Ejecutados de primera mano: `pytest` (1.821 tests), `mypy` en dos corpus, `ruff`, los 10 subcomandos de la CLI en dos codificaciones, el consolidador en modo plan, y un análisis AST propio del grafo de imports. |
| **Proyecto listo para refactorizar** | **Sí, con tres decisiones previas.** El código es de alta calidad, está fuertemente tipado y tiene una red de seguridad estructural real: refactorizar es seguro. Pero **tres correcciones cambian contratos y, según tu Fase 10, no las toco sin tu confirmación**: (1) el bloqueo `ci_green`, (2) la fecha en el artefacto derivado —C-3, cuatro alternativas evaluadas—, y (3) el alcance por capas de P6. Todo lo demás de la lista §11 es corrección contenida y puedo abordarlo sin cambiar arquitectura. |

### Nota sobre la ejecución de esta auditoría

Lancé un workflow de 12 dimensiones con refutación adversarial. La máquina tiene 4 núcleos, lo que fija el tope de concurrencia en 2 agentes: tras ~50 minutos ninguno de los 24 había cerrado, y la proyección era de varias horas. **Lo detuve y completé las 12 fases yo mismo**, ejecutando cada comando de primera mano. Ninguna afirmación de este informe procede de un agente cuyo resultado no haya verificado: todo lo marcado `[HECHO]` lo he comprobado personalmente. Lo único marcado `[HEUR]` es la cifra de 95 símbolos públicos sin referencia externa (§5.4) y la consecuencia exacta del `readme` colgado (M-4), y lo señalo como tal en su sitio.

**No he escrito, editado ni borrado ningún fichero del repositorio.** Los dos ficheros que `git status` muestra modificados (`docs/ARCHITECTURE.md`, `pyproject.toml`) ya lo estaban al empezar, y son en sí mismos hallazgos (M-10 y §4.2).

---

¿Empiezo por los tres bloqueantes? Para C-3 mi recomendación es la opción B (fecha derivada del último commit de `configs/`); dime si prefieres otra y redacto el ADR antes de tocar el generador.