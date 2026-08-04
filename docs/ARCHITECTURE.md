<!-- ARCHIVO GENERADO. NO EDITAR. -->
<!-- Fuente: CONSTITUTION.md + configs/*.toml + decisions/*.toml -->
<!-- Regenerar: python scripts/generate_docs.py -->
# Arquitectura
Vista derivada de los contratos. Si algo de aqui contradice a `configs/`, el contrato tiene razon (P10).

## Principios
| Id | Principio |
|---|---|
| P1 | Todo resultado debe ser reproducible |
| P2 | Ningún módulo puede depender de una capa superior |
| P3 | Cada decisión arquitectónica queda registrada mediante un ADR |
| P4 | Todo comportamiento está gobernado por contratos declarativos |
| P5 | Cada dato tiene exactamente una fuente de verdad |
| P6 | Ningún componente introduce estado oculto |
| P7 | Todo artefacto derivado se regenera automáticamente |
| P8 | La promoción es técnica; la aprobación es humana |
| P9 | Las excepciones exigen justificación explícita y verificable |
| P10 | La arquitectura prevalece sobre la implementación |

Texto completo en [`CONSTITUTION.md`](../CONSTITUTION.md).

## Capacidades
| Capacidad | Responsable | Proposito | Paquetes |
|---|---|---|---|
| Analytics | Research | Medir, atribuir y reportar. | `analytics` |
| Discovery | Research | Explorar el espacio de arquitecturas y proponer candidatos. | `discovery`, `optimization` |
| Execution | Trading | Convertir intencion en orden y seguir su estado. | `broker`, `execution`, `live`, `paper` |
| Foundation | Platform | Primitivas, tipos y determinismo. No es una capacidad de negocio; es el sustrato. | `config`, `core`, `domain`, `events`, `shared` |
| Governance | Trading | Gobernar el ciclo de vida del zoo: elegibilidad, aprobacion, despliegue y archivo. | `promotion`, `storage` |
| Interface | Platform | Exponer los casos de uso a un humano o a otro sistema. | `interfaces` |
| Monitoring | Platform | Saber que esta pasando y que dejo de pasar. | `monitoring` |
| Orchestration | Platform | Componer las capacidades en casos de uso. No contiene reglas. | `application`, `container` |
| Research | Research | Convertir precio en features y features en senales, de forma pura y causal. | `research` |
| Risk | Trading | Traducir una decision direccional en exposicion admisible. | `portfolio` |
| Validation | Research | Decidir si un resultado es evidencia o es ruido. | `validation`, `walkforward` |

## Paquetes
| Paquete | Capa | Capacidad | Visibilidad | Proposito |
|---|---|---|---|---|
| `app/core` | core | Foundation | public | Primitivas. Raiz del grafo. |
| `app/events` | core | Foundation | public | Bus de eventos en proceso: evento, publicador, suscriptor, dispatcher y middleware. |
| `app/domain` | domain | Foundation | public | Entidades, objetos de valor y servicios de dominio. |
| `app/shared` | shared | Foundation | public | Puertos (Protocol) entre el dominio y el mundo. |
| `app/analytics` | engine | Analytics | public | Calculo de metricas y reporting. |
| `app/discovery` | engine | Discovery | public | Coordina la busqueda de arquitecturas. |
| `app/execution` | engine | Execution | public | Maquina de estados de ordenes, validacion y ruteo. |
| `app/optimization` | engine | Discovery | public | Grid, random, guiada, evolutiva. |
| `app/portfolio` | engine | Risk | public | Riesgo y dimensionamiento con vision de cartera. |
| `app/promotion` | engine | Governance | public | Elegibilidad, ranking, aprobacion, despliegue y archivo. |
| `app/research` | engine | Research | public | data, features, signals, strategies, backtest. |
| `app/validation` | engine | Validation | public | Permutacion, Monte Carlo, stress, reality check. |
| `app/walkforward` | engine | Validation | public | Particionado en folds, evaluacion y agregacion. |
| `app/broker` | infrastructure | Execution | public | Adaptadores de broker. |
| `app/config` | infrastructure | Foundation | public | Proveedores de configuracion: TOML, YAML, entorno y CLI. Unico lugar que lee ficheros de config. |
| `app/monitoring` | infrastructure | Monitoring | public | Metricas de runtime, auditoria y health checks. |
| `app/storage` | infrastructure | Governance | public | Zoo canonico, zoo espejo y repositorio de artefactos. |
| `app/application` | application | Orchestration | public | Casos de uso. Un paquete por caso: command, request, response, service, runner. |
| `app/container` | application | Orchestration | public | Inyeccion de dependencias, raiz de composicion y orden de ciclo de vida. |
| `app/live` | operation | Execution | public | Motor de ejecucion real. |
| `app/paper` | operation | Execution | public | Motor sobre feed en tiempo real, sin ejecucion real. |
| `app/interfaces` | interface | Interface | public | CLI y API. |

## Matriz de dependencias
| Paquete | Puede importar de |
|---|---|
| `core` | _nada_ |
| `events` | `core` |
| `domain` | `core` |
| `shared` | `core`, `domain` |
| `analytics` | `core`, `domain`, `shared` |
| `discovery` | `core`, `domain`, `shared`, `research`, `analytics`, `walkforward`, `optimization` |
| `execution` | `core`, `domain`, `shared` |
| `optimization` | `core`, `domain`, `shared`, `research`, `analytics`, `walkforward` |
| `portfolio` | `core`, `domain`, `shared` |
| `promotion` | `core`, `domain`, `shared`, `analytics`, `walkforward`, `validation` |
| `research` | `core`, `domain`, `shared` |
| `validation` | `core`, `domain`, `shared`, `research`, `analytics` |
| `walkforward` | `core`, `domain`, `shared`, `research`, `analytics` |
| `broker` | `core`, `domain`, `shared` |
| `config` | `core`, `domain`, `shared` |
| `monitoring` | `core`, `domain`, `shared`, `events` |
| `storage` | `core`, `domain`, `shared` |
| `application` | `core`, `domain`, `shared`, `research`, `portfolio`, `execution`, `analytics`, `walkforward`, `validation`, `optimization`, `discovery`, `promotion`, `broker`, `storage`, `monitoring` |
| `container` | `core`, `domain`, `shared`, `events`, `config`, `research`, `portfolio`, `execution`, `analytics`, `walkforward`, `validation`, `optimization`, `discovery`, `promotion`, `broker`, `storage`, `monitoring` |
| `live` | `core`, `domain`, `shared`, `research`, `portfolio`, `execution`, `broker`, `storage`, `monitoring`, `application` |
| `paper` | `core`, `domain`, `shared`, `research`, `portfolio`, `execution`, `broker`, `storage`, `monitoring`, `application` |
| `interfaces` | `core`, `application`, `container` |

Verificado en `tests/test_architecture.py`. Toda arista no declarada rompe el build.

## Por que cada frontera esta donde esta

### `app/application`

La unica capa que ve todo, porque su trabajo es exactamente componer. No
contiene reglas. Si un runner empieza a hacer aritmetica de negocio, esa
aritmetica pertenece a `domain.services`.

### `app/broker`

Un adaptador implementa `BrokerPort` (declarado en shared) y habla el
vocabulario del dominio. La maquina de estados de la orden vive en
`domain.entities.order`, no en `execution`, asi que no necesita execution. Si
lo importara, cambiar la orquestacion obligaria a revisar todos los adaptadores.

### `app/config`

La configuracion esta partida en dos por pureza, no por capricho (ADR-0005).

`core.config` contiene el esquema tipado, los valores por defecto, el resolvedor
de precedencia y el validador. Todo puro: opera sobre diccionarios ya cargados y
no sabe de donde vienen. Los motores pueden importarlo.

`config` -este paquete- contiene los proveedores que leen disco, entorno y
argumentos. Es infraestructura, nivel 4, asi que NINGUN motor puede importarlo:
un motor de nivel 3 que pudiera leer un fichero de configuracion introduciria una
entrada no declarada y el resultado dejaria de depender solo de (datos, config,
semilla). Los motores reciben configuracion ya resuelta y tipada.

### `app/container`

Capa `application` porque para resolver un puerto hay que conocer al adaptador
concreto, y eso solo es legitimo en la raiz de composicion. Es el unico paquete
del sistema autorizado a saber que `BrokerPort` lo implementa MT5.

Regla que hace cumplir: nada se instancia directamente. Un motor nunca escribe
`Broker(...)`; escribe `container.resolve(BrokerPort)`. Sin esto la inversion de
dependencias se pierde justo donde importa, porque el motor pasa a conocer una
clase concreta y deja de ser sustituible en un test.

Absorbe tambien el orden de arranque y parada. `LifecyclePort` declara las seis
fases -initialize, load, warmup, start, stop, dispose- y el contenedor las
ejecuta en orden topologico de dependencias: un componente no arranca antes de
aquello de lo que depende, y se para en orden inverso. Un paquete `lifecycle`
separado solo contendria seis firmas de metodo y anadiria una arista al grafo sin
crear ninguna frontera real (ADR-0006).

### `app/core`

No importa nada del proyecto. Es lo que hace la regla verificable y no una
aspiracion: si core fuera hoja solo 'casi siempre', cualquier ciclo podria
esconderse detras de esa excepcion.

### `app/discovery`

Discovery no descubre: COORDINA. Los descubrimientos los hacen motores
especializados -generacion, evolucion, evaluacion, seleccion- y discovery
decide en que orden intervienen y con que presupuesto.

Discovery tampoco promociona. La promocion es gobierno del zoo, no parte del
proceso evolutivo, y vive en `promotion`. Mezclarlas haria que el buscador
tuviera autoridad para poner algo en produccion, que es exactamente la
separacion que un proceso cientifico necesita mantener.

### `app/domain`

No conoce infraestructura ni motores. Un objeto de dominio debe poder
construirse y validarse en un test sin disco, sin red y sin reloj.

### `app/events`

En capa `core` porque el bus es un mecanismo puro: encaminar un evento no toca
disco ni red. Los suscriptores que si hacen I/O viven en `monitoring` y se
registran desde `container`.

Situarlo aqui es lo que permite el desacoplamiento que se busca. Ningun motor
conoce al siguiente: `research` emite `FeaturesCalculated` y no sabe si alguien
escucha. Si el bus estuviera en infraestructura, un motor de nivel 3 no podria
emitir sin violar la matriz, y acabariamos pasando callbacks a mano.

El bus NO puede depender de `domain`: si conociera `Trade` u `Order`, cada evento
nuevo del dominio obligaria a tocar el bus. Los eventos transportan carga
generica y quien la interpreta es el suscriptor.

### `app/execution`

Execution NO conoce walkforward. La ejecucion es un mecanismo, la validacion es
una metodologia. Si el mecanismo conociera la metodologia, no se podria usar el
mismo motor en vivo.

### `app/interfaces`

`core` para que la CLI convierta "M15" en `Timeframe.M15` antes de invocar el
caso de uso; sin eso pasaria cadenas crudas y la validacion se desplazaria hacia
dentro.

`container` porque el punto de entrada es quien construye la raiz de composicion:
lee la configuracion, arma el contenedor y le pide el runner. Es el unico lugar
donde eso puede ocurrir, y es la razon de que `main.py` no contenga logica.

Se mantiene la prohibicion importante: la interfaz NO ve motores. No puede llamar
a discovery, research ni execution directamente. Solo casos de uso.

### `app/monitoring`

`events` esta en `depends` por ADR-0008. La arista ya estaba decidida en la
prosa de `packages.events` -"los suscriptores que si hacen I/O viven en
`monitoring`"- y faltaba en la tabla que leen las herramientas, de modo que un
suscriptor no podia ni tipar el `Event` que recibe.

Apunta hacia abajo: `events` es capa `core` (rango 0) y `monitoring` es
`infrastructure` (rango 4). No hay ascenso de capa y no puede introducir ciclos.

Lo que NO cambia: `monitoring` no aparece en el `depends` de ningun motor. Un
motor no puede importar observabilidad; emite por `EventSinkPort`, declarado en
`shared`, y el adaptador concreto lo inyecta `container`.

### `app/promotion`

Subsistema propio, no una carpeta dentro de discovery. Consume la evidencia
(walkforward, validation, analytics) pero NO depende de discovery: una estrategia
escrita a mano debe poder recorrer el mismo camino de promocion que una generada
por la busqueda.

No depende de research ni de execution: no calcula y no ejecuta. Decide.

Tampoco depende de `storage`, aunque el resultado de una promocion acabe en el
zoo. `storage` es infraestructura (nivel 4) y promotion es motor (nivel 3):
declararlo haria que la dependencia subiera de capa, y eso es precisamente lo
que P2 prohibe. Promotion escribe a traves de `StrategyRepositoryPort`, declarado
en `shared`, y el runner de `application` inyecta el adaptador concreto.

La consecuencia practica es la correcta: promotion emite un veredicto y no sabe
si se persiste en Parquet, en DuckDB o en memoria durante un test.

### `app/research`

Research NO conoce broker. Si el calculo de un indicador pudiera consultar al
broker, el backtest dejaria de ser reproducible y la suite no podria ejecutarse
sin conexion.

### `app/shared`

Los puertos hablan el vocabulario del dominio, por eso lo importan. No importan
ningun adaptador: el sentido del puerto es que el adaptador dependa de el.

### `app/storage`

El zoo no es un JSON de parametros: es un catalogo con historia. Cada entrada
guarda metadatos, metricas, estado de ciclo de vida, historial de aprobaciones,
hash de reproducibilidad, hash del dataset y linaje de investigacion. Sin esa
historia, una estrategia en produccion es un numero sin procedencia.

## Contrato de comportamiento
| Garantia | Principio | Motivo |
|---|---|---|
| `auditable_rejections` | P3 | Todo descarte lleva `ReasonCode`. Un cero en un array de senal no distingue "no habia setup" de "fuera de sesion", y esas dos cosas exigen decisiones distintas del investigador. |
| `causal_features_only` | P1 | `feature[i]` depende solo de `bars[0..i]`. Una decision tomada con la barra `i` se ejecuta como pronto en la apertura de `i+1`. Verificado en tests/test_no_lookahead.py perturbando el futuro. |
| `costs_always_applied` | P9 | No existe una metrica bruta en `PerformanceMetrics`. Es deliberado: hace imposible presentar por descuido un resultado sin costes. |
| `deterministic` | P1 | Mismas entradas, misma configuracion y misma semilla producen el mismo resultado, bit a bit. Sin esto no se puede afirmar que un cambio mejoro nada. |
| `immutable_domain` | P6 | Todo objeto de dominio es inmutable y valida sus invariantes al construirse. Asi "si existe, es correcto" pasa a ser una propiedad del tipo y no una esperanza. Verificado en tests/test_conventions.py. |
| `no_global_mutable_state` | P6 | Ninguna variable de modulo mutable sin marcar `Final`. El estado compartido entre corridas es la forma mas silenciosa de romper el determinismo: la segunda corrida ve lo que dejo la primera. |
| `no_hidden_randomness` | P6 | La aleatoriedad se obtiene de `core.determinism.rng_for`, que deriva un generador aislado por consumidor. Un generador global hace que el resultado dependa del orden de ejecucion entre modulos. |
| `no_wall_clock` | P6 | El tiempo entra por `ClockPort`. Sin eso ningun backtest es reproducible y ninguna regla horaria es testeable sin esperar al reloj real. |
| `reproducible` | P1 | Toda corrida guarda semilla, hash de configuracion, hash de datos y version de codigo. Una corrida que no se puede repetir no es un experimento. |

## Modos de ejecucion
| Modo | Hereda de | Proposito |
|---|---|---|
| `deterministic` | `—` | Base de todos los demas. Determinismo absoluto, sin excepciones. |
| `research` | `deterministic` | Exploracion interactiva. Puede leer datos y escribir artefactos. |
| `benchmark` | `deterministic` | Medicion formal. Es el unico modo cuyos numeros pueden compararse entre corridas. |
| `ci` | `deterministic` | Verificacion automatica. Sin red, sin datos externos, sin broker. |
| `paper` | `research` | Feed en tiempo real, sin ejecucion real. |
| `live` | `paper` | Ejecucion real. El unico modo que puede mover dinero. |
| `demo` | `live` | Integracion tecnica contra un terminal real, sobre cuenta de demostracion. |

### Invariantes que ningun modo puede romper
- `broker_orders_require_promotion_and_approval` (P8)
- `live_accepts_only_promoted_states` (P8)
- `safeguards_never_discounted` (P10)

## Aislamiento de plugins

Un plugin solo ve: `app.core`, `app.domain`, `app.shared`. Nada mas.

## Decisiones
| Id | Estado | Fecha | Responsable | Titulo |
|---|---|---|---|---|
| [`ADR-0001`](../decisions/ADR-0001.toml) | accepted | 2026-07-26 | Platform | La arquitectura es un modelo declarativo, no el codigo |
| [`ADR-0002`](../decisions/ADR-0002.toml) | accepted | 2026-07-26 | Research | Por el dominio circulan arrays de numpy, no DataFrames |
| [`ADR-0003`](../decisions/ADR-0003.toml) | accepted | 2026-07-26 | Platform | El catalogo de componentes vive en core; el descubrimiento, aparte |
| [`ADR-0004`](../decisions/ADR-0004.toml) | accepted | 2026-07-26 | Platform | Existe una Constitucion que gobierna los contratos |
| [`ADR-0005`](../decisions/ADR-0005.toml) | accepted | 2026-07-26 | Platform | La configuracion se parte en dos por pureza: resolucion en core, lectura en infraestructura |
| [`ADR-0006`](../decisions/ADR-0006.toml) | accepted | 2026-07-26 | Platform | La Fase 3 construye una plataforma; los motores no empiezan hasta la Fase 4 |
| [`ADR-0007`](../decisions/ADR-0007.toml) | accepted | 2026-07-26 | Platform | La arquitectura se congela por stress implementation, con registro de hallazgos |
| [`ADR-0008`](../decisions/ADR-0008.toml) | accepted | 2026-07-28 | Platform | Monitoring depende de events: la arista que el propio contrato ya declaraba en prosa |
| [`ADR-0009`](../decisions/ADR-0009.toml) | accepted | 2026-07-30 | Platform | El sello de los artefactos derivados no depende del tiempo fisico |
| [`ADR-0010`](../decisions/ADR-0010.toml) | accepted | 2026-07-30 | Platform | El contrato de resultado se cierra antes de que exista el primer motor |
| [`ADR-0011`](../decisions/ADR-0011.toml) | accepted | 2026-07-31 | Platform | La cadena de datos se parte en tres piezas que no se conocen entre si |
| [`ADR-0012`](../decisions/ADR-0012.toml) | accepted | 2026-08-02 | Platform | La Fase 4 se abre con la 3 en curso, y el fichero de entrega deja de poder mentir |
| [`ADR-0013`](../decisions/ADR-0013.toml) | accepted | 2026-08-02 | Platform | El primer recorrido ejecutable, y la identidad de un artefacto de corrida |
| [`ADR-0014`](../decisions/ADR-0014.toml) | accepted | 2026-08-03 | Trading | El riesgo dimensiona sobre capital y stop, y separa lo que recorta de lo que rechaza |
| [`ADR-0015`](../decisions/ADR-0015.toml) | accepted | 2026-08-03 | Platform | Los contratos de dominio entran en la configuracion resuelta, y la prosa se queda fuera |
| [`ADR-0016`](../decisions/ADR-0016.toml) | accepted | 2026-08-03 | Research | La semilla de cada fold se deriva, no se suma |
| [`ADR-0017`](../decisions/ADR-0017.toml) | accepted | 2026-08-04 | Trading | Demo es live con una salvaguarda mas, no con una menos |

---

Generado por `scripts/generate_docs.py` desde `configs/architecture.toml` v3.
