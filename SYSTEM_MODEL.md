# Modelo del sistema

Responde a una pregunta distinta de la de los demás documentos:

> ¿Qué **es** exactamente QuantPlatform, y qué modelos conceptuales debe
> implementar todo el código?

No describe estructura de carpetas ni proceso de construcción. Describe el
dominio. Es lo que se lee **antes** de escribir una línea del núcleo cuantitativo.

Existe porque sin él, cinco personas implementan cinco cosas distintas bajo el
nombre «señal», y las cinco parecen razonables. Las definiciones de aquí son
normativas: si el código contradice una, el código está mal.

| Documento | Pregunta |
|---|---|
| `CONSTITUTION.md` | ¿Qué principios no sacrificamos? |
| `SYSTEM_MODEL.md` | ¿Qué es el sistema, conceptualmente? |
| `BUILD.md` | ¿Cómo se construye y en qué orden? |
| `configs/architecture.toml` | ¿Cómo está organizado el código? |

---

## 1. Definiciones

### Estrategia

Una composición determinista de capacidades cuantitativas que transforma una
secuencia temporal de observaciones de mercado en una secuencia temporal de
decisiones candidatas, respetando invariantes matemáticas y operacionales.

No es una clase, ni un archivo, ni un módulo. Es un **dato**: una composición de
bloques con parámetros, cuya identidad es el hash de su contenido. Dos
estrategias son la misma si su composición canónica es la misma, aunque las haya
escrito gente distinta con años de diferencia.

### Feature

Una función pura `Bars → FeatureVector`. Nada más.

No decide. No conoce posiciones ni riesgo ni broker. Dado el mismo input,
produce el mismo output, siempre, hasta el último bit.

### Señal

Transforma features en **hipótesis**, no en órdenes. Responde `LONG`, `SHORT` o
`FLAT` con una confianza en `[0, 1]` y un motivo por barra.

No conoce riesgo, ni tamaño, ni ejecución, ni broker, ni las demás posiciones
abiertas. Si una señal necesitara saber cuánto capital hay, no sería una señal:
sería una decisión de cartera disfrazada.

### Filtro

Decide `permitida` o `rechazada`. **Nunca modifica una señal y nunca crea una.**

La distinción no es sutil. Un filtro que ajusta la confianza en lugar de vetar
se convierte en un generador de señales encubierto, y entonces el embudo de
descarte deja de poder explicarse: no se puede contar cuántos setups eliminó
algo que en realidad los transformaba.

### Riesgo

Su trabajo **empieza** cuando la señal ya existe. Traduce una dirección en
exposición admisible: tamaño, stop, objetivo, apalancamiento.

No calcula indicadores. No busca patrones. No hace aprendizaje automático. Si el
módulo de riesgo mira el precio para decidir *si* operar, ha invadido la señal.

### Cartera

Sabe que existen oportunidades simultáneas y decide **cuáles sobreviven**.

No sabe cómo se generó cada señal, y es deliberado: si lo supiera, empezaría a
preferir unas familias sobre otras por razones que ya evaluó la validación.

### Ejecución

Implementa decisiones. **Jamás toma ninguna.**

Es el único componente autorizado a hablar con el broker. Si la ejecución
decidiera algo —saltarse una orden por spread, reducir tamaño—, esa decisión
quedaría fuera de todo lo validado y fuera de toda auditoría.

### Discovery

**No busca estrategias. Busca arquitecturas.**

Lo que evoluciona es el ensamblaje completo —features + señales + filtros +
riesgo + cartera—, no un indicador aislado ni los parámetros de una plantilla
fija. Un buscador que solo ajusta el periodo de una media no está descubriendo:
está sobreajustando con más pasos.

### Validación

**Nunca mejora una estrategia. Intenta destruirla.** Si sobrevive, continúa.

Cualquier procedimiento que use el resultado de la validación para ajustar la
estrategia convierte el out-of-sample en in-sample, y a partir de ese momento
todos los números posteriores son ficción.

### Promoción

**No evalúa rentabilidad. Evalúa confianza.**

Una curva ascendente con veinte operaciones no es evidencia. La pregunta no es
«¿ganó?» sino «¿cuánto de esto puede explicarse por azar?».

### Live

**No ejecuta estrategias. Ejecuta estrategias certificadas.**

Certificada significa promovida —técnicamente— y aprobada —humanamente—. Son dos
autoridades distintas y ninguna sustituye a la otra.

---

## 2. Objetos fundamentales

Cada uno declara lo que debe definir cualquier implementación.

### Bar / Bars

Objeto de valor inmutable, no un `DataFrame`.

- `timestamp[i]` es la **apertura**; la barra se conoce completa en
  `timestamp[i] + timeframe`.
- Invariantes verificadas al construir: orden estrictamente creciente, `high`
  envuelve a `open`/`close`, `low` los envuelve por abajo, sin `NaN` en precios,
  volumen no negativo.
- Buffers no escribibles. Si existe un `Bars`, sus datos son correctos.

### Feature

Debe definir: `input`, `output`, `warmup`, determinismo, complejidad, coste y
dependencias.

`warmup` es el más importante y el que más se olvida: es el número de barras
iniciales no calculables, y walk-forward lo descarta en cada fold. Declararlo por
debajo del real contamina el out-of-sample con arrastre del in-sample.

Las primeras `warmup` posiciones son `NaN`, nunca cero. Cero es un valor legítimo
y confundirlo con «aún no calculable» genera señales fantasma justo al inicio de
cada fold.

### Signal

Debe definir: dirección, confianza, motivo por barra y procedencia de features.

El motivo (`ReasonCode`) no es opcional. Un cero sin motivo no distingue «no
había setup» de «fuera de sesión» de «aún en calentamiento», y esas tres
situaciones exigen decisiones distintas del investigador.

### Position

Debe definir su máquina de estados y sus transiciones legales. Registra MAE y
MFE: una estrategia con MFE alto y resultado bajo tiene un problema de salida,
no de entrada.

### Order

Tres momentos distintos, y confundirlos es un error clásico:

- **Intent** — lo que la estrategia quiere. Puro dominio, sin broker.
- **Execution** — lo que existe en el mundo, con identidad y estado.
- **Settlement** — el llenado confirmado.

`decided_at` es el cierre de la barra que originó la decisión, no el momento de
envío. Esa diferencia es lo que hace auditable el retardo de ejecución.

### Trade

Debe definir apertura, gestión, cierre y PnL desglosado.

`gross_pnl` y `net_pnl` se guardan por separado siempre. Su diferencia es la
métrica que revela estrategias cuyo edge desaparece al pagar el mercado.

`exit_reason` es campo de primera clase: si el 90 % de las salidas son
`STOP_LOSS`, la lógica de salida no está aportando nada.

### Dataset

Debe definir identidad, huella, versión, fuente, zona horaria e integridad.

Mismo símbolo y mismo rango con huella distinta significa que la serie se
reprocesó, y cualquier comparación contra resultados anteriores deja de ser
válida.

---

## 3. Motores

Cada motor se especifica como sistema, no como carpeta: responsabilidad,
entradas, salidas, invariantes, errores, eventos, puertos, métricas,
configuración, casos de uso, y **qué le está prohibido**.

| Motor | Entrada | Salida | Prohibido |
|---|---|---|---|
| Research | Bars, calendario | `ResearchContext` | Generar señales |
| Feature | Bars, `ResearchContext` | `FeatureVector` | Decidir |
| Signal | `FeatureVector` | `SignalOutput` | Riesgo, broker, cartera |
| Filter | `SignalOutput` | Señal aprobada | Modificar o crear señales |
| Risk | Señal aprobada, cartera | `OrderIntent` | Indicadores, decidir *si* operar |
| Portfolio | `OrderIntent[]` | Subconjunto | Análisis técnico |
| Execution | `OrderIntent` | `Order`, `Trade` | Decidir |
| Analytics | `Trade[]`, equity | Métricas, informes | Influir en la ejecución |
| Discovery | Catálogo, presupuesto | Candidatos | Promocionar |
| Validation | Candidato, evidencia | Veredicto | Mejorar la estrategia |
| Promotion | Veredicto, evidencia | Decisión + constancia | Calcular, ejecutar |

La columna «prohibido» es la que hay que leer primero. Define la frontera mejor
que la descripción de la responsabilidad.

---

## 4. Flujo completo

De la vela al trade cerrado:

```
Broker → Bars → Data Validation → Research → Feature Engine → Feature Store
      → Signal Engine → Filter Engine → Risk Engine → Portfolio Engine
      → Execution Engine → Broker → Position Manager → Trade Manager
      → Analytics → Storage → Monitoring → Promotion
```

Cada flecha es un contrato. Nunca se salta una.

**Regla temporal que atraviesa todo el flujo:** una decisión tomada con datos de
la barra `i` se ejecuta como pronto en la apertura de la barra `i+1`. Sin esa
regla, el backtest opera al precio que motivó la decisión y el resultado es
ficción.

---

## 5. Invariantes globales

Nunca puede existir ninguna de estas dependencias:

| Prohibido | Por qué |
|---|---|
| Feature → Broker | El backtest dejaría de ser reproducible sin que nada lo delate |
| Signal → MT5 | Ata la hipótesis a un terminal concreto |
| Risk → Indicators | El riesgo estaría decidiendo *si* operar, no *cuánto* |
| Portfolio → Análisis técnico | Reevaluaría lo que ya evaluó la señal |
| Execution → Decisión | La decisión quedaría fuera de lo validado y de la auditoría |
| Validation → Estrategia (escritura) | Convierte el out-of-sample en in-sample |
| Discovery → Promotion | Daría al buscador autoridad para desplegar |
| Cualquiera → Reloj de pared | Rompe determinismo y hace intestables las reglas horarias |

Las siete primeras son consecuencia de las definiciones de la sección 1. La
última es P6.

Todas están o estarán verificadas por `tests/test_architecture.py` contra la
matriz de dependencias. Una invariante conceptual que no tenga su arista
prohibida en `configs/architecture.toml` es una invariante que se violará.

---

## 6. Cómo se usa este documento

Antes de implementar cualquier pieza del núcleo cuantitativo:

1. Localizar su definición en la sección 1.
2. Comprobar qué tiene **prohibido** en la sección 3.
3. Verificar que ninguna dependencia necesaria aparece en la sección 5.
4. Si algo de lo anterior obliga a contradecir el documento, es un hallazgo
   `structural` de la Fase 4.5 y exige ADR. No se resuelve reinterpretando la
   definición.

El paso 4 es el que da valor a los otros tres.
