# Constitución

Este documento responde a una única pregunta:

> ¿Qué principios no estamos dispuestos a sacrificar, aunque la implementación
> cambie por completo?

No describe paquetes. No describe reglas. No describe tests. Está por encima de
todo eso.

La jerarquía de gobierno es:

```
CONSTITUTION.md          principios irrenunciables
      ↓ gobierna
configs/*.toml           contratos declarativos
      ↓ gobierna
app/                     implementación
      ↑ verifica
tests/                   comprobación mecánica
```

Cada principio tiene un identificador estable. Los contratos de `configs/` los
citan en sus tablas `principle`, y `tests/test_governance.py` comprueba dos
cosas: que todo principio esté citado por al menos un contrato, y que toda cita
resuelva. Un principio que ningún contrato invoca es retórica; una cita a un
principio inexistente es una regla huérfana.

Modificar este documento es el cambio más grave que admite el proyecto. Exige
un ADR con estado `accepted` y la lista de contratos afectados.

---

## P1 — Todo resultado debe ser reproducible

Mismas entradas, misma configuración y misma semilla producen el mismo
resultado. Toda corrida registra semilla, hash de configuración, hash de datos y
versión de código.

Una corrida que no puede repetirse no es un experimento: es una anécdota. Sin
este principio no se puede afirmar que un cambio mejoró nada, porque no existe
forma de separar la mejora del ruido.

## P2 — Ningún módulo puede depender de una capa superior

Las dependencias apuntan siempre hacia dentro. El dominio no conoce
infraestructura; el cálculo no conoce el broker; la investigación no conoce la
ejecución.

Este principio es lo que permite sustituir cualquier pieza externa sin tocar la
lógica, y lo que hace posible ejecutar la suite completa sin conexión y sin
datos de mercado.

## P3 — Cada decisión arquitectónica queda registrada mediante un ADR

Toda decisión estructural existe como dato en `decisions/`, con responsable,
fecha, validación y plan de reversión. Una decisión reemplazada no se borra: se
marca y se apunta a la que la sustituye.

El historial de por qué se cambió de opinión tiene tanto valor como la decisión
vigente. Sin él, la siguiente persona repite un análisis que ya se hizo y llega
a la conclusión que ya se descartó.

## P4 — Todo comportamiento está gobernado por contratos declarativos

Una restricción existe primero como contrato legible y verificable, y solo
después como código. La arquitectura no es `app/`: es `configs/`. El código es
su reflejo.

Un documento no puede romper un build, y lo que no rompe el build se ignora bajo
presión de entrega. Un contrato verificado sí.

## P5 — Cada dato tiene exactamente una fuente de verdad

Los contratos se dividen solo cuando describen sujetos distintos. Nunca cuando
describen el mismo sujeto desde ficheros diferentes.

Dos ficheros que describen el mismo hecho acaban discrepando. Cuando eso ocurre,
el test comprueba uno mientras la documentación promete el otro, y eso es peor
que no tener gobernanza: parece que la hay.

## P6 — Ningún componente introduce estado oculto

Sin variables de módulo mutables. Sin generadores de números aleatorios
globales. Sin reloj de pared en las capas puras. El tiempo entra por un puerto;
la aleatoriedad, por una semilla derivada.

El estado compartido entre corridas es la forma más silenciosa de romper el
determinismo: la segunda corrida ve lo que dejó la primera, y nada lo delata.

## P7 — Todo artefacto derivado se regenera automáticamente

Documentación, diagramas, reportes e informes son vistas, no fuentes. Se
generan desde los contratos en cada build. Una edición manual de un artefacto
derivado desaparece en la siguiente generación.

Un artefacto que se mantiene a mano se desincroniza. Cuando describe la
arquitectura, además, se convierte en una segunda fuente de verdad y viola P5.

## P8 — La promoción es técnica; la aprobación es humana

Una estrategia puede ser estadísticamente promovible y operativamente
rechazada: un activo deshabilitado, un límite de capital, una restricción legal,
un riesgo que nadie quiere asumir.

Son dos decisiones distintas y las toman dos autoridades distintas. Fusionarlas
quita el último control antes de que el sistema mueva dinero, y le da al
buscador automático autoridad para desplegar.

## P9 — Las excepciones exigen justificación explícita y verificable

Ninguna regla se silencia sin motivo escrito junto a la excepción. Ningún
umbral existe sin la razón por la que es ese número. Nada de `# noqa` ni
`# type: ignore` sin argumento de arquitectura.

Dentro de un año alguien verá `forbid_random = true` y preguntará por qué. Si no
hay respuesta, la regla deja de ser una decisión y pasa a ser una superstición.
Una plataforma institucional no admite supersticiones.

## P10 — La arquitectura prevalece sobre la implementación

Cuando el código y el contrato discrepan, el contrato tiene razón y el código se
corrige. Nunca al revés.

Es el principio que sostiene a los otros nueve. Sin él, cada uno se erosiona en
el primer momento de urgencia, y la erosión no ocurre de golpe: ocurre una
excepción a la vez, y cada una parece razonable cuando se concede.

---

## Lo que esta Constitución no dice

No fija tecnologías. No fija estructura de carpetas. No fija umbrales
concretos. Todo eso vive en los contratos y puede cambiar sin tocar este
documento, que es exactamente la propiedad que se busca: los principios son
estables porque no dependen de ninguna decisión técnica particular.

Si un cambio de implementación obliga a reescribir un principio, la sospecha por
defecto es que el cambio está mal, no el principio.
