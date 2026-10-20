# Evidencia de validación

Fecha: **20 de octubre de 2026**. Revisión local de contratos y documentación. No se ingresó a portales de organismos ni se ejecutaron trámites, pagos o consultas con credenciales reales.

## Inventario confirmado

Se importaron `CATALOGUE`, `OPERATIONS` y el registro real de plugins:

- 34 bots públicos y 45 operaciones públicas.
- 34 plugins de catálogo. Diferencia de nombres entre catálogo y registro: vacía.
- Una variante de AGIP no publicada como bot independiente.
- Las operaciones `consulta` de `consulta_cuit` y `mis_comprobantes` requieren traducción del verbo al ejecutar. No se descartaron del inventario.

## Pruebas existentes

Se utilizó el entorno virtual existente del repositorio, sin instalar dependencias ni cambiar su configuración.

| Comando | Resultado observado |
|---|---|
| `.venv/bin/python -m pytest -q` | Falló la colección por módulos de test homónimos, incluido `test_worker_capacity.py`. No representa pruebas funcionales fallidas. |
| `.venv/bin/python -m pytest -q --import-mode=importlib` | Falló la colección por imports de utilidades locales como `fakes_playwright` y `runner`. |
| `.venv/bin/python -m pytest -q packages/mrbot-contracts/tests` | 11 aprobadas. |
| `.venv/bin/python -m pytest -q services/bot-worker/tests` | 784 aprobadas. |
| `.venv/bin/python -m pytest -q services/central-api/tests` | 615 aprobadas, 30 omitidas y 5 subtests aprobados. |
| `.venv/bin/python -m pytest -q tests` | 119 aprobadas y 10 subtests aprobados. |
| `.venv/bin/python -m pytest -q tools/compat_runner` | 11 aprobadas. |

**Total por invocaciones aisladas:** 1.540 pruebas aprobadas, 30 omitidas y 15 subtests aprobados adicionales. Las omitidas no se consideran verificadas. Se observaron advertencias de deprecación y una opción de configuración de pruebas no reconocida. No se alteraron tests para ocultarlas.

## Casos reproducidos sin servicios externos

| Caso | Método | Observación |
|---|---|---|
| Confirmación de callback | `send_result()` con cliente falso y una respuesta por código. | 200, 302, 401, 403, 409 y 422 devolvieron `True`; 503 devolvió `False`. |
| Crecimiento de cachés | Admitir y liberar 1.000 `LocalJob`; registrar 1.000 resultados en `ResultStore`. | 0 activos, 1.000 admisiones cacheadas y 1.000 resultados cacheados. |
| Colisión en ZIP | ZIP temporal con `carpeta-a/reporte.txt` y `carpeta-b/reporte.txt`, usando `expandir_zip()`. | Se devolvió el mismo nombre dos veces. El archivo final contenía solo el segundo contenido. |
| Categorías y cuota | Handler real `report_result()` con persistencia desactivada, job local y operaciones de liquidación interceptadas. | `CHROMIUM_CRASHED`, `ARTIFACT_UPLOAD_FAILED` e `INTERNAL` eligieron confirmación. Solo `INFRASTRUCTURE_ERROR` eligió reembolso. La condición persistente equivalente se revisó en código, no con una cuenta real. |
| Falta de campo requerido en ruta canónica | Handler real `create_job()` de `ccma/consultar`, `payload: {}`, credenciales ficticias y reserva interceptada. | Respondió 202/PENDIENTE. El payload conservado produjo `InvalidInputError` en el plugin por faltar `representado_cuit`. |
| Defaults efectivos de SCT/SIPER | Normalizadores reales, separación de credenciales y `plugin.validate()`, enviando solo `representado_cuit`. | SCT agregó las tres secciones, `formatos: ["xlsx"]` y `subir: false`. SIPER agregó `subir: false`, detalle y categorías activos. El default del schema aislado no describe el pedido canónico completo. |
| CUIT de sesión no sustituye al representado en SIPER canónico | `payload: {}` con CUIT únicamente en `credentials`, aplicando el mismo pipeline local. | La validación falló por faltar `representado_cuit`; el fallback del normalizador solo ve campos del payload. |
| Precedencia de aliases de VEP CCMA | Payload con `representado_cuit` y `cuit_representado` distintos, `subir_pdf: true` y `minio_upload: false`. | El normalizador conservó los nombres canónicos y descartó los aliases; el plugin validó el resultado. No todas las contradicciones se rechazan: algunas desaparecen antes del schema. |

Las reproducciones con transporte falso, reserva interceptada o estado en memoria son **pruebas sintéticas**, no una validación del despliegue completo. La limpieza tras callback agotado, la pérdida de advertencias y el alcance del timeout se trazaron en el runner y el receptor terminal.

## Ejemplos generados por el catálogo actual

Se verificaron los 45 ejemplos producidos por `_operation_documentation()` aplicando:

1. `normalize_v2_payload()`.
2. `normalize_payload_and_credentials(..., require_encryption=False)` para separar datos ficticios, sin exigir una clave de custodia del despliegue.
3. Traducción local de referencias `*_key` a nombres de entrada según `KEY_FIELDS`, y nombres locales para `archivos`. No se descargó contenido ni se comprobó existencia o pertenencia de objetos.
4. Traducción del verbo con `worker_operation()`.
5. `plugin.validate()`, **sin llamar a `execute()`**.

Resultado: **42 válidos y 3 incompletos**:

| Bot/operación | Motivo del rechazo |
|---|---|
| `carga_portal_iva/cargar` | El ejemplo no incluye un par de TXT o un CSV de apertura requerido. |
| `controladores_fiscales/presentar` | El ejemplo no incluye `archivos`. |
| `vep_archivo/generar` | El ejemplo no permite obtener el `archivo_path` requerido. |

Este resultado pertenece a los ejemplos **generados por el código previo**, no a una promesa de que todas las solicitudes admitidas por la API pasan validación. Las fichas nuevas deben explicar la carga previa y proporcionar referencias ficticias completas donde corresponda.

## Verificadores históricos

- `infra/validate-code-blocks.py`: los 189 bloques de planes que revisa parsean correctamente. Ese resultado no equivale a validar todos los bloques de la nueva carpeta `docs/`.
- `infra/check-consistency.py`: se ejecutó durante la creación de las fichas y reportó errores, entre ellos enlaces todavía no creados, enlaces históricos y estilo en archivos de terceros bajo entornos locales. No se considera un pase de aceptación ni se atribuye todo el resultado al estado anterior. La nueva documentación requiere su comprobación focalizada de enlaces y contenido una vez terminada.

## Límites

No se levantó infraestructura, no se ejecutaron migraciones y no se cambió código operativo. Las pruebas con base de datos que la suite omitió, el almacenamiento real, la entrega tras caída de procesos, los portales, la disponibilidad por cuenta, la concurrencia de producción y los efectos fiscales requieren otra validación en un entorno autorizado.
