# `bots_dev` del worker V3

Cada plugin productivo registrado en `bot_worker.bots.registry.REGISTRY` tiene
una subcarpeta con `NOTA.md` y `payload_ejemplo.json`. La suite
`tests/test_bots_dev_fixtures.py` comprueba que la cobertura coincida con el
registro y que cada ejemplo pase `plugin.validate` sin abrir navegador, llamar
servicios externos ni ejecutar operaciones fiscales.

Estos ejemplos prueban el contrato de entrada, no la operación remota. Algunas
cargas, como `carga_portal_iva`, usan bytes ilustrativos y no son documentos
fiscales válidos para enviar al organismo.

## Diferencia con `bots_dev` legacy de V1 y V2

V1 y V2 tienen 46 carpetas cada uno y comparten los mismos nombres de primer
nivel. En la comparación de nombres y alias, 32 carpetas se relacionan con un
plugin productivo V3. Quedan 14 sin plugin V3 identificado:

- Herramientas o infraestructura: `arca_login`, `bot_builder`, `proxy_login_tester`, `shared`, `tests`.
- Proyectos no representados como plugin productivo: `beneficios_mipyme`, `carga_931`, `carga_libro_iva`, `cartilla_medica_union_personal`, `efectores_pami`, `habilitar_servicio`, `portal_iva_ddjj_y_libros`, `tucuman`, `xubio_sueldos`.

La coincidencia de nombres no demuestra paridad funcional. Los proyectos no
representados no se copian al registro de V3: requieren migración al contrato
`BotPlugin`, revisión de seguridad y pruebas propias antes de exponerse como
operaciones de la API.

### Auditoría estática de proyectos sin plugin

La revisión de V1/V2 confirma que no es seguro declarar estos nueve proyectos
como funcionales por su mera presencia en `bots_dev`. Los equivalentes citados
son cercanos por dominio o formato, no aliases con paridad demostrada:

| Proyecto legacy | Plugin V3 más cercano | Bloqueo observado y gate previo |
|---|---|---|
| `beneficios_mipyme` | `certificado_mipyme`, solo por dominio | El legacy incluye extracción/exportación. Separar beneficios de LUFE y validar el portal antes de exponerlo. |
| `carga_931` | `carga_portal_iva`, solo como analogía de carga | Variantes draft/full, período fijo y ruta absoluta de control. Elegir una variante y revisar cualquier borrado antes de integrar. |
| `carga_libro_iva` | `carga_portal_iva` para carga y `libros_portal_iva` para descarga | Batch con variantes; `eliminar_todo` puede borrar movimientos antes de importar. Exigir dry-run, idempotencia y pruebas de fallo parcial. |
| `cartilla_medica_union_personal` | Ninguno identificado | El directorio contiene un draft y logger; falta un flujo productivo validado y pruebas contra contrato V3. |
| `efectores_pami` | Ninguno identificado | Existe implementación legacy, pero no se demostró equivalencia ni aceptación del portal en V3. |
| `habilitar_servicio` | Ninguno identificado | Es una operación con efecto sobre servicios fiscales; requiere revisión explícita del alcance y cuenta/sandbox autorizados. |
| `portal_iva_ddjj_y_libros` | `libros_portal_iva`, solapamiento parcial | El wrapper legacy tiene una dependencia de importación no confirmada. Alinear períodos y DDJJ/libros no basta para afirmar alias. |
| `tucuman` | `declaracion_en_linea`, analogía débil | Hay un draft de presentación anual con credenciales hardcodeadas. Sanitizar y validar en sandbox antes de cualquier presentación. |
| `xubio_sueldos` | Ninguno identificado | Combina flujo UI, modo API interno y probes de desarrollo; falta contrato, gestión segura de tokens y pruebas de artefactos. |

Estos hallazgos son inspección estática de V1/V2, no una prueba de ejecución ni
una afirmación de que los portales sigan compatibles. Las operaciones fiscales
con efectos externos permanecen fuera del registro productivo V3 hasta migrar,
probar sus fallos/efectos y completar una aceptación autorizada. Las carpetas
`arca_login`, `bot_builder`, `proxy_login_tester`, `shared` y `tests` se
clasifican aparte como herramientas/infraestructura, no plugins productivos.
