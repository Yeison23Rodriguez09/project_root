# Especificación maestra de construcción

Responde a una única pregunta:

> ¿Cómo se construye QuantPlatform desde un repositorio vacío hasta una
> plataforma certificada?

No describe el código que existe. Describe **el proceso**: los hitos, las reglas
de promoción entre fases y los criterios de aceptación. Es la referencia para
cualquier persona o agente que retome el proyecto sin haber estado en las
primeras etapas.

Existe porque el conocimiento implícito no sobrevive. En un proyecto de esta
duración, quien construyó la Fase 2 no será quien construya la Fase 7, y sin
este documento la Fase 7 se construiría adivinando las reglas.

## Dónde encaja

| Documento | Pregunta que responde | Frecuencia de cambio |
|---|---|---|
| `CONSTITUTION.md` | ¿Qué principios no sacrificamos nunca? | casi nula |
| `SYSTEM_MODEL.md` | ¿Qué es el sistema, conceptualmente? | muy baja |
| `BUILD.md` | ¿Cómo se construye, en qué orden y con qué criterios? | baja |
| `configs/architecture.toml` | ¿Cómo está organizado el código? | congelada |
| `configs/delivery.toml` | ¿Qué está construido y qué falta? | diaria |
| `decisions/*.toml` | ¿Por qué se decidió cada cosa? | por decisión |

`SYSTEM_MODEL.md` es de lectura obligatoria antes de escribir cualquier pieza del
núcleo cuantitativo. Define qué es una señal, un filtro o el riesgo, y qué le
está prohibido a cada uno. Sin él, cinco personas implementan cinco cosas
distintas bajo el mismo nombre y las cinco parecen razonables.

Los cuatro primeros gobiernan; el último explica. Ninguno describe el estado
operativo: eso se **calcula** con `qp doctor`, nunca se declara.

---

## Regla de oro

**Ninguna fase se abre antes de cerrar la anterior.**

Cerrar significa que todas las capacidades de esa fase están en `ready` según la
Definition of Done de `configs/delivery.toml`, con los criterios `blocking`
cumplidos sin excepción.

El motivo es el fallo concreto del proyecto anterior: estrategias, discovery,
zoo, optimización, MT5, live, runtime, configuración y arquitectura evolucionaron
a la vez. Los acoplamientos que eso generó resultaron imposibles de deshacer. La
estructura debe impedir que se repita, no confiar en que no se repita.

---

## Secuencia

### Fase 0 — Constitución

Fijar los principios irrenunciables. Sin código.

**Aceptación:** existe `CONSTITUTION.md` con principios identificados, y
`ADR-0004` los establece.

### Fase 1 — Arquitectura

Declarar paquetes, capas, capacidades y dependencias. Sin lógica.

**Aceptación:** `configs/architecture.toml` completo; `pytest -m contract`
verde en lo que respecta a coherencia interna de la matriz.

### Fase 2 — Gobernanza

Convertir los contratos en pruebas que rompen el build.

**Aceptación:** validador AST operativo, ciclos e imports relativos prohibidos,
ADR con esquema verificable, documentación derivada en sincronía.

### Fase 2.5 — Consolidación

Un solo árbol, sin duplicados, sin código muerto, imports saneados.

**Aceptación, en este orden exacto:**

```
python scripts/consolidate_architecture.py            # revisar el plan
python scripts/consolidate_architecture.py --apply
python scripts/generate_docs.py
pytest -m "contract or unit"
git tag architecture-baseline
```

Hasta que ese tag exista, la Fase 3 no está abierta formalmente.

### Fase 3 — Plataforma

El sistema operativo de la plataforma. Sin trading.

Orden interno **por dependencias**, no por carpetas:

```
Configuration → Container → Lifecycle → Events → Plugins → Runtime → Preflight
```

Cada bloque necesita **un consumidor real dentro de la propia Fase 3** antes de
considerarse cerrado. El bus se prueba con los eventos de configuración; el
contenedor, resolviendo `ClockPort`. Un bloque sin consumidor es especulación y
se recorta.

**Aceptación:** en una máquina limpia,

```
git clone … && pip install -e . && qp doctor
→ Platform READY
```

sin broker, sin datos de mercado, sin una sola estrategia.

### Fase 3.5 — Validación de plataforma

Certificar que la plataforma es autoconsistente antes del primer indicador.

**Aceptación:** doble ejecución con la misma semilla produce artefactos
idénticos bit a bit; `ConfigFingerprint` estable; todas las capacidades de
plataforma en `ready`.

### Fase 4 — Motor cuantitativo

Ahora sí. Anatomía única, sin excepciones:

```
Research → Features → Signals → Filters → Risk → Portfolio → Execution → Analytics
```

y dentro de cada motor:

```
Runner → Request → Validation → Service → Domain → Ports → Response → Events → Metrics
```

Nunca se salta una capa. Nunca se accede a infraestructura directamente. Nada se
instancia directo: `container.resolve(BrokerPort)`, jamás `Broker(...)`.

### Fase 4.5 — Congelación arquitectónica

El criterio cambia. Deja de ser *«¿falta algún componente?»* y pasa a ser
*«¿puede construirse un sistema real sin tocar la arquitectura?»*.

Entregable único: el recorrido completo, de MT5 a Analytics, usando solo lo que
existe. Se permiten implementaciones y correcciones; no capas, paquetes,
traslados ni cambios de contrato.

**Registro de hallazgos, no veredicto binario.** Cada necesidad de cambio se
anota en `reports/freeze_findings.md` con severidad `cosmetic`, `contained`,
`structural` o `fundamental`. Se congela con cero `structural` y cero
`fundamental`; los `contained` se cuentan porque miden la fricción real que
impone el diseño. Un criterio binario solo consigue que el primer hallazgo
incómodo se reclasifique.

**El recorrido termina en paper, no en live.** Ejecutar live como criterio de
aceptación sería usar dinero real como prueba de diseño, y exigiría promover una
estrategia que no ha pasado validación científica — rompiendo la puerta de
promoción para demostrar que la arquitectura funciona. Paper ejercita el mismo
runner, los mismos puertos y el mismo ciclo de vida; solo cambia quién implementa
`BrokerPort`. Detalle en `ADR-0007`.

**Precondiciones.** No se abre sin: Fase 2.5 ejecutada, una ejecución real de la
suite, contenedor operativo, bus de eventos existente, loader invocado y
`qp doctor` en verde. Sin ellas, 4.5 mediría la ausencia de implementación en
lugar de la calidad del diseño.

### Fases 5 a 10

Discovery, validación estadística, live, escalabilidad. Cada una con la misma
regla de cierre.

### Después de 4.5 — el SDK

Superada la congelación, el siguiente documento maestro ya no describe
arquitectura: es la guía de desarrollo sobre la plataforma. Cómo se crea una
estrategia, se registra una feature, se añade un broker, se distribuye un plugin
y se promueve al zoo. A partir de ahí el proyecto deja de construir la plataforma
y pasa a construir productos sobre ella.

---

## Promoción entre fases

Una fase se promueve cuando se cumplen las cuatro condiciones:

1. Todas sus capacidades en `ready` en `configs/delivery.toml`.
2. Todos los criterios `blocking` de la DoD satisfechos.
3. `qp doctor` en verde para los modos que la fase habilita.
4. Un ADR aceptado que registre el cierre, con la evidencia.

La cuarta no es burocracia: es lo que permite, dos años después, saber qué se
consideraba suficiente en aquel momento y si el criterio ha cambiado desde
entonces.

---

## Cómo se añade algo nuevo

El orden es siempre el mismo, y es el inverso del habitual:

1. **Principio** — si lo que se añade no sirve a ningún principio de la
   Constitución, probablemente no deba existir.
2. **Contrato** — declararlo en `configs/`: paquete, capa, capacidad,
   dependencias.
3. **ADR** — si la decisión es estructural, registrarla con validación y plan de
   reversión.
4. **Test** — la prueba antes que la implementación, porque la prueba es la que
   fija el contrato.
5. **Código** — el último artefacto, no el primero.
6. **Regenerar** — `python scripts/generate_docs.py`.

Si el paso 5 obliga a cambiar el paso 2, el diseño estaba mal y se corrige el
contrato explícitamente. Nunca se ajusta el contrato en silencio para que encaje
con el código: eso invierte P10.

---

## Señales de que el proceso se está degradando

Cada una tiene una acción asociada. No son advertencias generales.

| Señal | Qué significa | Acción |
|---|---|---|
| Un test de contrato desactivado | La gobernanza dejó de gobernar | Restaurarlo o retirar la regla con ADR |
| Una excepción sin `why` | Empezó la erosión por excepciones | Justificarla o eliminarla |
| Documentación editada a mano | Un artefacto derivado se volvió fuente | Regenerar y averiguar por qué se editó |
| Un paquete sin fila en la matriz | Hay código no gobernado | Declararlo antes de seguir |
| Dos fases abiertas a la vez | Se repite el fallo del proyecto anterior | Cerrar la primera |
| Un `blocked` que lleva semanas | El bloqueo no tiene dueño | Asignarlo o cambiar de estrategia |

---

## Estado actual

Fases 0, 1 y 2 completas en contenido. **Fase 2.5 sin ejecutar**: coexisten dos
jerarquías (`core/` y `app/core/`, `domain/` y `app/domain/`) y ninguna prueba se
ha lanzado. Por eso la capacidad `Foundation` está en `blocked` en
`configs/delivery.toml`, con los comandos de desbloqueo escritos dentro.

Fase 3 iniciada de forma parcial: configuración con procedencia y fingerprint,
puertos de persistencia y ciclo de vida, `preflight` y `qp doctor`. Ninguno
verificado todavía.

La Fase 4 permanece cerrada.
