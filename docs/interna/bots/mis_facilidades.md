# Auditoría interna: `mis_facilidades`

## Contrato y ruta

Catálogo central: operación única `consultar` (`services/central-api/src/central_api/api/bots.py:L406`). Alias `/api/v3/mis_facilidades/consulta` apunta a la misma (`central-api/api/bot_compat.py:L73`). La ruta genérica usa sobre `{payload, credentials}` y responde con creación asíncrona (`central-api/api/bots.py:L466-L513`, `L748-L817`).

## Flujo esquema → plugin → portal

1. **Entrada.** Modelo estricto (`services/bot-worker/src/bot_worker/bots/mis_facilidades/schema.py:L27-L28`). CUIT representado opcional, alias `cuit`, normaliza separadores y, si falta o es `null`, recurre al CUIT representante (`L31-L42`, `L48-L53`). `denominacion` opcional, null por default, no vacío si enviado, hasta 128 (`L54`). `situacion_excluyente` lista default vacía, máximo 20, alias legado; `incluir_pdf`, `incluir_xlsx`, `subir_archivos` default true; requiere al menos un formato PDF/XLSX (`L55-L83`).
2. **Plugin.** Puede aplicar defaults de configuración solo cuando el payload no define el campo (`plugin.py:L149-L181`). Exige credenciales, consulta CUIT disponibles antes de elegir el CUIT objetivo, usa CUIT de credencial si el representado no se especificó y luego recolecta datos/errores (`L183-L229`).
3. **Portal.** `runtime/portals/mis_facilidades.py:L134-L235` lee planes y situación. El plugin omite siempre los planes cancelados y aplica las exclusiones por subcadena sin distinguir mayúsculas/minúsculas (`plugin.py:L231-L361`). El portal exporta PDFs por vistas y XLSX con tablas visibles (`runtime/portals/mis_facilidades.py:L243-L333`).

## Normalizaciones y outputs

Resultado incluye `cuits_disponibles`, planes/procesados y errores por plan o sección. Cada PDF se produce por vista disponible; las tablas se ensamblan en XLSX. Si falla una parte, se conserva el resto y estado se vuelve `PARCIAL`; sin errores, `OK` (`plugin.py:L207-L229`, `L242-L361`). `subir_archivos=false` deja generación/localización temporal pero sin `files[]`. `situacion_excluyente=[]` no filtra. Un campo vacío de CUIT no es `None`, pasa por limpieza y falla.

## Discrepancias observadas

- La documentación/modelo público V3 expresa CUIT representado como regex estricto de 11 dígitos (`services/central-api/src/central_api/api/bot_payloads.py:L213-L219`), pero el validador real acepta separadores al limpiarlos (`worker schema.py:L31-L42`).
- Los defaults V2 y V3 para `subir_archivos` no son equivalentes en todas las rutas: V2 usa `carga_minio=true`, V3 schema usa `true`; el comportamiento queda consistente para falta de entrada, pero la llave/contracto público difiere (`central-api/api/bot_payloads.py:L556-L578`, worker `schema.py:L61-L67`).
- El catálogo de credenciales no expone propiedades requeridas (`central-api/api/bots.py:L820-L845`), pese al guard del plugin (`plugin.py:L183-L190`).

## Referencias y pruebas revisadas

- `services/bot-worker/src/bot_worker/bots/mis_facilidades/schema.py:L27-L117`
- `services/bot-worker/src/bot_worker/bots/mis_facilidades/plugin.py:L104-L229`, `L231-L361`
- `services/bot-worker/src/bot_worker/runtime/portals/mis_facilidades.py:L134-L235`, `L243-L333`
- `services/central-api/src/central_api/api/bots.py:L406`, `L466-L513`, `L820-L845`
- `services/central-api/src/central_api/api/bot_compat.py:L73`
- `services/central-api/src/central_api/api/bot_payloads.py:L213-L219`, `L556-L578`
- Revisados: `services/bot-worker/tests/test_portal_mis_facilidades.py` y tests del plugin. No se ejecutó ninguna sesión real.
