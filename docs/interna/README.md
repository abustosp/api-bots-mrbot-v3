# Documentación interna

**Uso exclusivo del equipo.** Revisión del repositorio: 20 de octubre de 2026. Estos documentos contienen nombres de tecnologías, rutas fuente y limitaciones operativas que no forman parte del manual del usuario.

## Mapa de lectura

- [API y recorrido de los datos](api.md): rutas, autenticación, validación, normalización, persistencia y consulta de resultados.
- [Workers](workers.md): admisión, ejecución, capacidad, leases, archivos, cancelación, limpieza y reporte.
- [Schemas y models](schemas-models.md): discrepancias y propuestas concretas de mejora. No son cambios ya implementados.
- [Auditoría priorizada](auditoria.md): casos de fallo comprobados, corrección mínima propuesta e impacto. El informe utiliza inglés sencillo conforme al formato de auditoría solicitado.
- [Validaciones realizadas](validacion.md): comandos, resultados y límites de la revisión.

## Fuente de verdad y carga asumida

Se asume una API multiusuario con varias solicitudes simultáneas, workers de 5 ejecuciones por defecto y procesos de larga duración. No se extrapola el comportamiento del respaldo en memoria al despliegue persistente. Se distingue el catálogo público del manifiesto de ejecución y de las copias de modelos de compatibilidad.

Los planes y README históricos son contexto, no pruebas de implementación. La documentación se basa en rutas, normalizadores, schemas, plugins, portales y tests. No se ejecutan trámites reales. No hay cambios de código operativo, migraciones o dependencias en esta entrega.

## Trazabilidad por bot

Cada ficha interna debe leerse junto a la ficha del mismo nombre de `docs/externa/bots/` al preparar un cambio de contrato. Las rutas públicas pueden utilizar verbos distintos de los del plugin.

| Bot | Análisis interno |
|---|---|
| `apoc` | [Flujo y contrato](bots/apoc.md) |
| `aportes_en_linea` | [Flujo y contrato](bots/aportes_en_linea.md) |
| `arba` | [Flujo y contrato](bots/arba.md) |
| `carga_portal_iva` | [Flujo y contrato](bots/carga_portal_iva.md) |
| `ccma` | [Flujo y contrato](bots/ccma.md) |
| `ccma_compensacion` | [Flujo y contrato](bots/ccma_compensacion.md) |
| `certificado_mipyme` | [Flujo y contrato](bots/certificado_mipyme.md) |
| `compensaciones` | [Flujo y contrato](bots/compensaciones.md) |
| `comprobantes` | [Flujo y contrato](bots/comprobantes.md) |
| `consulta_cuit` | [Flujo y contrato](bots/consulta_cuit.md) |
| `consulta_pagos_vep` | [Flujo y contrato](bots/consulta_pagos_vep.md) |
| `controladores_fiscales` | [Flujo y contrato](bots/controladores_fiscales.md) |
| `declaracion_en_linea` | [Flujo y contrato](bots/declaracion_en_linea.md) |
| `facturometro` | [Flujo y contrato](bots/facturometro.md) |
| `hacienda` | [Flujo y contrato](bots/hacienda.md) |
| `libro_sueldos_digital` | [Flujo y contrato](bots/libro_sueldos_digital.md) |
| `libros_portal_iva` | [Flujo y contrato](bots/libros_portal_iva.md) |
| `liquidacion_granos` | [Flujo y contrato](bots/liquidacion_granos.md) |
| `mis_comprobantes` | [Flujo y contrato](bots/mis_comprobantes.md) |
| `mis_facilidades` | [Flujo y contrato](bots/mis_facilidades.md) |
| `mis_retenciones` | [Flujo y contrato](bots/mis_retenciones.md) |
| `mis_retenciones_iva_simple` | [Flujo y contrato](bots/mis_retenciones_iva_simple.md) |
| `moa` | [Flujo y contrato](bots/moa.md) |
| `pago_devoluciones` | [Flujo y contrato](bots/pago_devoluciones.md) |
| `portal_iva` | [Flujo y contrato](bots/portal_iva.md) |
| `rcel` | [Flujo y contrato](bots/rcel.md) |
| `retper_iibb_agip` | [Flujo y contrato](bots/retper_iibb_agip.md) |
| `retper_iibb_misiones` | [Flujo y contrato](bots/retper_iibb_misiones.md) |
| `sct` | [Flujo y contrato](bots/sct.md) |
| `sifere` | [Flujo y contrato](bots/sifere.md) |
| `siper` | [Flujo y contrato](bots/siper.md) |
| `srt` | [Flujo y contrato](bots/srt.md) |
| `vep_archivo` | [Flujo y contrato](bots/vep_archivo.md) |
| `vep_ccma` | [Flujo y contrato](bots/vep_ccma.md) |

## Regla de publicación

Distribuir solo la carpeta `docs/externa/`. Antes de publicar, comprobar que no contiene tecnologías, rutas de código, referencias a informes internos, credenciales reales, detalles de provisión ni diagnósticos reservados. Los nombres heredados de un parámetro pueden requerir describir su efecto sin explicar la infraestructura a la que aludían.

## Criterio para actualizar contratos

1. Fijar el comportamiento observable deseado por operación, sin asumir que un default del modelo local llega igual desde la API.
2. Expresar requerido, nullable, formato, límites, default, dependencias y efecto de vacío por cada campo.
3. Definir por separado nombre canónico y aliases heredados, con una única normalización verificable.
4. Especificar las salidas reales a través de la frontera de reporte, no solo `BotResult`.
5. Verificar ejemplos, rechazos, parcialidad y archivos. No usar credenciales o portales reales para probar contratos.
6. Modificar modelos ejecutables únicamente en una tarea de implementación autorizada y acompañada de pruebas de regresión.
