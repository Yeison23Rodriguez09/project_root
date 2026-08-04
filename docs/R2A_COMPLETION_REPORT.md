# R2A — Informe de cierre

**Etapa 1 del pipeline cuantitativo: MT5 → Descargar → Validar → Guardar → DatasetRepository**

Rama `fase-4/r2-mt5-adapter` · ADR-0011 `accepted` · 2026-07-31

Este documento no es un artefacto derivado: se escribe una vez y no lo regenera
`scripts/generate_docs.py`.

---

## 1. Arquitectura final

```
                        MetaTrader 5  (terminal con sesion del operador)
                              |
        ..................... | .....................  app/broker
        :                     v                     :
        :        MT5MarketDataAdapter               :   implementa MarketDataPort
        :        conecta · verifica · descarga      :   NO guarda nada
        :.....................|.....................:
                              v
                            Bars            <- valida sus invariantes al construirse
                              |
        ..................... | .....................  app/application/download
        :                     v                     :
        :        HistoricalStorageService           :   UNICO coordinador
        :        compone linaje · decide que escribe:
        :.......|.....................|.............:
                v                     v
   MarketDataWriterPort      DatasetRepositoryPort     <- app/shared (contratos)
                |                     |
        ....... | ................... | .............  app/research/data
        :       v                     v               :
        : ParquetMarketDataWriter  ParquetDatasetCatalog
        :       |                     |               :
        :       +----- DatasetLayout -+               :   convencion COMPARTIDA
        :       +----- identity ------+               :   una sola huella
        :.............................................:
                              ^
                              |
                     ParquetMarketData          <- lector de R1b, misma convencion
```

### Piezas y responsabilidad única

| Componente | Paquete | Implementa | Responsabilidad |
|---|---|---|---|
| `MT5MarketDataAdapter` | `broker` | `MarketDataPort` | Habla con el terminal. No guarda |
| `ParquetMarketData` | `research.data` | `MarketDataPort` | Lee de disco. No repara |
| `ParquetMarketDataWriter` | `research.data` | `MarketDataWriterPort` | Serializa. No valida ni cataloga |
| `ParquetDatasetCatalog` | `research.data` | `DatasetRepositoryPort` | Indexa. No materializa |
| `DatasetLayout` | `research.data` | — | Ubicación, naming, organización |
| `identity.content_hash_of` | `research.data` | — | Identidad de serie, definición única |
| `HistoricalStorageService` | `application.download` | — | **Único** que coordina |
| `WriteResult` | `domain` | — | 4 hechos técnicos de una escritura |
| `MarketDataWriterPort` | `shared` | — | Contrato público nuevo |
| `qp download` | `interfaces.cli` | — | Traduce argumentos. Sin lógica |

**Ningún adaptador conoce a otro.** La fuente no sabe que se persiste, el escritor
no sabe de dónde vino la serie, el catálogo no sabe quién la escribió.

### Ubicaciones impuestas por el contrato, no elegidas

- `MT5MarketDataAdapter` está en `broker` porque `architecture.toml` **prohíbe
  `MetaTrader5` en `research`**.
- La composición está en `application.download.runner` y no en `container`:
  `test_matrix_is_respected` rechazó que `container` importara `application`.
- El reloj no tiene valor por defecto en la fábrica: `application` tampoco puede
  importar `container`, donde vive el reloj concreto.

**Ningún contrato se modificó para acomodar el código. El código se movió.**

---

## 2. Verificación

| Herramienta | Resultado |
|---|---|
| `pytest` | **2175 total · 1941 pasan · 234 saltados · 0 fallos** |
| `mypy app` | **Success — 122 ficheros** |
| `ruff check .` | **All checks passed** |

### Cobertura de los componentes de R2A

| Módulo | Sentencias | Sin cubrir | Cobertura |
|---|---:|---:|---:|
| `application/download/service.py` | 20 | 0 | **100 %** |
| `application/download/runner.py` | 19 | 0 | **100 %** |
| `application/download/response.py` | 19 | 0 | **100 %** |
| `research/data/parquet_writer.py` | 46 | 0 | **100 %** |
| `research/data/layout.py` | 22 | 0 | **100 %** |
| `research/data/identity.py` | 9 | 0 | **100 %** |
| `domain/value_objects/dataset.py` | 23 | 0 | **100 %** |
| `research/data/catalog.py` | 60 | 2 | 97 % |
| `research/data/parquet_source.py` | 54 | 4 | 93 % |
| `broker/mt5_source.py` | 107 | 7 | 93 % |
| **TOTAL** | **380** | **13** | **97 %** |

Las 7 líneas sin cubrir de `mt5_source.py` son, con nombre y apellido:

- **94-101** — el import perezoso de `MetaTrader5`. Solo se ejecuta cuando **no**
  se inyecta terminal, que es precisamente lo que la batería nunca hace. Cubrirla
  exigiría desinstalar el paquete durante el test.
- **193** — `return ()` cuando `symbols_get()` devuelve `None`.
- **328** — la rama `default=0` de la conversión de fechas.

Ninguna oculta lógica de negocio. Se declaran en lugar de redondear el número.

---

## 3. Tests creados

**68 tests nuevos**, todos ejecutables **sin terminal MT5**.

| Fichero | Tests | Qué fija |
|---|---:|---|
| `tests/test_mt5_adapter.py` | **25** | Contrato del adaptador MT5 contra doble |
| `tests/test_historical_storage_service.py` | **13** | Orquestación, con dobles de los 4 puertos |
| `tests/test_market_data_writer.py` | **12** | Serialización, atomicidad, huella |
| `tests/test_dataset_catalog.py` | **11** | Indexado y detección de reprocesamiento |
| `tests/test_dataset_layout.py` | **7** | Convención compartida lector/escritor |
| `tests/fakes/mt5.py` | — | Doble del módulo `MetaTrader5` |

Contexto de la batería completa de dominio y datos, incluyendo lo anterior:

| Fichero | Tests |
|---|---:|
| `tests/test_domain_result_contract.py` | 34 |
| `tests/test_domain_bars_contract.py` | 16 |
| `tests/test_market_data_source.py` | 15 |

### Lo que la batería garantiza, y no es obvio

**El doble devuelve arrays estructurados de numpy**, que es la forma exacta en
que `copy_rates_*` publica los datos. Un doble que devolviera un diccionario
haría pasar los tests y fallar contra el terminal real.

**Las tres comprobaciones de conexión se prueban por separado** —terminal caído,
sesión ausente, servidor desconectado— porque confundirlas manda a reinstalar
cuando lo que falta es iniciar sesión.

**El servicio se prueba con dobles, no con adaptadores reales.** Con piezas
reales, un test verde no distinguiría entre «el servicio orquesta bien» y «los
adaptadores compensan un fallo del servicio».

**Se prueba lo que los componentes NO hacen**: no ordenan filas, no rellenan
huecos, no interpolan `NaN`, no corrigen timestamps, no reparan OHLC. Y la
contrapartida: un hueco de fin de semana **pasa intacto**.

**El adaptador no acepta credenciales**, fijado como aserción sobre su firma para
que añadirlas sea una decisión visible y no un parámetro más.

---

## 4. Verificación contra terminal real

Fuera de la suite, como comprobación de **aceptación** y no de regresión:

```
Terminal: XMGlobal-MT5 7 · login 316760851 · 1581 símbolos

qp download --symbol EURUSD --timeframe M15 --from 2024-01-01 --to 2024-03-01
→ 4129 barras · 138.328 bytes · huella c3d203f1b9de8d42 · EXIT=0

qp download --symbol EURUSD --timeframe H1 --from 2024-06-01 --to 2024-07-01
→ 481 barras · 24.029 bytes · EXIT=0
```

La serie M15 tiene **8 huecos, que son exactamente los 8 fines de semana del
periodo**, y todos los saltos son múltiplo del timeframe. La invariante de
rejilla de R1a se sostiene con datos de mercado reales.

---

## 5. Riesgos restantes

### Alto — zona horaria del servidor MT5

MT5 publica el instante de apertura **en la zona horaria del servidor del
broker**, no necesariamente en UTC. **No se aplica corrección.**

El riesgo es de una clase concreta: un desplazamiento uniforme **no rompe ninguna
invariante de `Bars`** —el orden se mantiene, la rejilla se mantiene, el OHLC se
mantiene—, así que pasaría inadvertido y contaminaría cada backtest posterior.
Inventar la corrección sin medirla desplazaría toda la serie.

**Debe medirse contra un instrumento de horario conocido antes de que Discovery
consuma estos datos.** Está documentado en `app/broker/mt5_source.py`.

### Medio — el adaptador MT5 no se ejercita en CI

Por diseño: `runtime.toml` exige `require_synthetic_data_only` en modo `ci`. La
batería lo cubre al 93 % contra un doble, pero **el doble puede divergir del
módulo real** sin que nada lo detecte. Mitigación actual: el doble reproduce la
forma exacta de `copy_rates_*`. Mitigación pendiente: R2B.

### Bajo — profundidad de descarga no acotada por el contrato

Sin rango se piden `MAX_BARS_WITHOUT_RANGE = 200_000` barras. MT5 entrega solo lo
que tenga descargado, así que es un techo y no una promesa. Un usuario podría
creer que obtuvo «todo el histórico» cuando obtuvo lo que su terminal tenía. El
linaje registra el rango pedido frente al obtenido, que hace la diferencia
auditable, pero nada la señala en la salida del comando.

### Bajo — `datasets.json` no es concurrente

Dos `qp download` simultáneos sobre la misma raíz pueden pisarse el índice. No es
un problema hoy —el comando es interactivo— y lo será cuando algo lo invoque en
paralelo.

### Deuda registrada, fuera del alcance de R2A

Descarga incremental, deduplicación y versionado. El puerto no los impide y el
servicio es el sitio donde entrarán.

---

## 6. Checklist DoD

| Criterio | Estado | Evidencia |
|---|---|---|
| `MT5MarketDataAdapter` implementa exactamente `MarketDataPort` | ✅ | `isinstance` sobre el `Protocol`, sin herencia |
| Conecta, verifica terminal, verifica login | ✅ | 3 comprobaciones con diagnóstico distinto |
| Lista símbolos | ✅ | Orden fijado, no el del terminal |
| Descarga OHLCV y convierte a `Bars` | ✅ | Segundos → nanosegundos sin redondeo |
| **No guarda nada** | ✅ | El adaptador no conoce escritor ni catálogo |
| `qp download` funcional | ✅ | Verificado contra terminal real y proveedor FILE |
| Persistencia en `DatasetRepository` | ✅ | `datasets.json` con huella y linaje |
| ADR | ✅ | `decisions/ADR-0011.toml`, `accepted` |
| Tests | ✅ | 68 nuevos, todos sin terminal |
| Toda la batería sin MT5 real | ✅ | 68/68 en ejecución aislada |
| `pytest` | ✅ | 2175 total, **0 fallos** |
| `mypy` | ✅ | Success, 122 ficheros |
| `ruff` | ✅ | All checks passed |
| Puertos y adaptadores | ✅ | 1 puerto nuevo, 4 adaptadores |
| Sin romper contratos existentes | ✅ | Ninguna API pública modificada |
| Documentación derivada regenerada | ✅ | `test_derived_docs_are_in_sync` verde |

---

## 7. Confirmación

**R2A queda cerrada.**

La Etapa 1 del pipeline —MT5 → Descargar → Validar → Guardar → DatasetRepository—
está implementada, verificada contra el terminal real, cubierta al 97 % por una
batería que no necesita terminal, y registrada en ADR-0011.

Con dos salvedades declaradas, no ocultas: **la zona horaria del servidor MT5
sigue sin medir**, y debe resolverse antes de que Discovery consuma estos datos;
y el adaptador MT5 se ejercita en CI contra un doble, no contra el terminal.

Nada de Discovery, Optimize, WalkForward, Zoo, Paper ni Live se ha implementado.
