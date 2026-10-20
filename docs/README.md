# Documentación de MrBot

Revisión del código: **20 de octubre de 2026**.

Esta carpeta separa dos audiencias:

| Carpeta | Destinatario | Contenido |
|---|---|---|
| [interna](interna/README.md) | Desarrollo, soporte técnico y operación | API, workers, trazas por bot, diferencias de contratos y auditoría. |
| [externa](externa/README.md) | Usuarios e integradores | Qué hace cada bot, datos de entrada, valores vacíos, resultados, archivos y efectos de cada opción. |

**Para compartir con usuarios, distribuir únicamente `docs/externa/`.** Esa carpeta es autocontenida y no requiere enlaces al código o a la documentación interna. No distribuir esta raíz, los planes, los prototipos ni los informes internos como parte del manual público.

## Alcance y criterio

- Se revisan los **34 bots registrados y sus 45 operaciones públicas**. Una variante de ejecución no se cuenta como otro producto.
- La implementación actual prevalece sobre los planes históricos. La documentación describe el comportamiento vigente, no cambios futuros.
- Una solicitud aceptada no significa que el trámite haya terminado. Las salidas y los archivos dependen del resultado de cada operación.
- Se distingue entre omitir una propiedad, enviar `null`, una cadena vacía, espacios, `false`, `0` o una lista vacía. No hay una regla universal entre bots ni entre las rutas de compatibilidad y las canónicas.
- Las credenciales y ejemplos son ficticios. No se realizan trámites, pagos ni consultas reales para redactar esta documentación.
- Esta entrega modifica documentación, no el comportamiento ni los modelos ejecutables.

## Mantenimiento

Ante un cambio de campo, revisar juntos: catálogo/ruta pública, normalización de compatibilidad, modelo público, validación del plugin, uso efectivo en el portal y proyección del resultado público. Actualizar ambas fichas del bot, validar los ejemplos y no copiar detalles de implementación hacia `externa/`.

El informe de auditoría y la evidencia de verificaciones están en [interna/auditoria.md](interna/auditoria.md) e [interna/validacion.md](interna/validacion.md).
