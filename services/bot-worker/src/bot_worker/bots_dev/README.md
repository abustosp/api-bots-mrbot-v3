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
plugin productivo V3. Quedan 14 sin plugin de nombre exacto en el registro V3:

- Herramientas o infraestructura: `arca_login`, `bot_builder`, `proxy_login_tester`, `shared`, `tests`.
- Proyectos no representados como plugin productivo: `beneficios_mipyme`, `carga_931`, `carga_libro_iva`, `cartilla_medica_union_personal`, `efectores_pami`, `habilitar_servicio`, `portal_iva_ddjj_y_libros`, `tucuman`, `xubio_sueldos`.

Las rutas legacy V3 `/libros_iva/consulta` y `/libros_iva/ddjj` delegan a
`libros_portal_iva` para descargar libros y DDJJ, respectivamente. Esa capacidad
ya existe mediante el plugin V3 y aliases centrales, pero no hace que el wrapper
combinado `portal_iva_ddjj_y_libros` sea un plugin con contrato 1:1. La
coincidencia de nombres tampoco demuestra paridad funcional. Los proyectos que
no estén cubiertos por un flujo equivalente requieren migración al contrato
`BotPlugin`, revisión de seguridad y pruebas propias antes de exponerse como
operaciones de la API.

### Auditoría estática de proyectos sin plugin propio

Las nueve carpetas de proyectos no tienen un plugin V3 con el mismo nombre.
Una de ellas, `portal_iva_ddjj_y_libros`, tiene cubiertas sus dos operaciones de
descarga por las rutas legacy citadas, aunque su wrapper combinado y su entrada
por lista de períodos no son idénticos. La inspección de las otras ocho carpetas
confirma que no es seguro declararlas funcionales por su mera presencia en
`bots_dev`:

| Proyecto legacy | Plugin V3 más cercano | Bloqueo observado y gate previo |
|---|---|---|
| `beneficios_mipyme` | `certificado_mipyme`, solo por dominio | El legacy incluye extracción/exportación. Separar beneficios de LUFE y validar el portal antes de exponerlo. |
| `carga_931` | `carga_portal_iva`, solo como analogía de carga | Variantes draft/full, período fijo y ruta absoluta de control. Elegir una variante y revisar cualquier borrado antes de integrar. |
| `carga_libro_iva` | `carga_portal_iva` para carga y `libros_portal_iva` para descarga | Batch con variantes; `eliminar_todo` puede borrar movimientos antes de importar. Exigir dry-run, idempotencia y pruebas de fallo parcial. |
| `cartilla_medica_union_personal` | Ninguno identificado | El directorio contiene un draft y logger; falta un flujo productivo validado y pruebas contra contrato V3. |
| `efectores_pami` | Ninguno identificado | Existe implementación legacy, pero no se demostró equivalencia ni aceptación del portal en V3. |
| `habilitar_servicio` | Ninguno identificado | Es una operación con efecto sobre servicios fiscales; requiere revisión explícita del alcance y cuenta/sandbox autorizados. |
| `portal_iva_ddjj_y_libros` | `libros_portal_iva`, mediante `/libros_iva/consulta` y `/libros_iva/ddjj` | Los aliases cubren ambas descargas. El wrapper local usa una lista de períodos y flags combinados, por lo que el contrato no es 1:1 ni se validó el portal en vivo. |
| `tucuman` | `declaracion_en_linea`, analogía débil | Hay un draft de presentación anual con credenciales hardcodeadas. Sanitizar y validar en sandbox antes de cualquier presentación. |
| `xubio_sueldos` | Ninguno identificado | Combina flujo UI, modo API interno y probes de desarrollo; falta contrato, gestión segura de tokens y pruebas de artefactos. |

Estos hallazgos son inspección estática de V1/V2, no una prueba de ejecución ni
una afirmación de que los portales sigan compatibles. Las operaciones fiscales
con efectos externos permanecen fuera del registro productivo V3 hasta migrar,
probar sus fallos/efectos y completar una aceptación autorizada. Las carpetas
`arca_login`, `bot_builder`, `proxy_login_tester`, `shared` y `tests` se
clasifican aparte como herramientas/infraestructura, no plugins productivos.
