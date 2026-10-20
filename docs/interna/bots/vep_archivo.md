# Auditoría interna: `vep_archivo`

## Flujo esquema → plugin → portal

1. **Esquema.** `services/bot-worker/src/bot_worker/bots/vep_archivo/schema.py:20-24` establece tamaño máximo/aliases; `:28-43` unifica CUIT si las variantes llegan juntas; `:64-80` unifica subida; `:82-131` define entrada, quita campos de sobre sellado y elimina `None` de varios flags antes de aplicar defaults; `:141-164` es JSON Schema menos completo que el modelo.
2. **Plugin.** `services/bot-worker/src/bot_worker/bots/vep_archivo/plugin.py:141-189` manifiesta operación/efecto fiscal, artefactos y configuración; `:191-257` valida, revisa archivo TXT, requiere credenciales y genera resultado; `:259-319` envía archivo al servicio, solicita volante, descarga PDF/QR y publica si corresponde.
3. **Portal real.** `services/bot-worker/src/bot_worker/runtime/portals/vep_archivo.py:8-13` contiene comentario desactualizado sobre QR; `:31-40` enumera medios; `:114-173` inicia generación y trata el click final sin reintento; `:174-216` prepara el formulario; `:243-318` procesa PDF/QR y cleanup. El servicio recibe el lote en TXT previamente cargado.

## Catálogo, alias y normalización central

`services/central-api/src/central_api/api/bots.py:419` publica `vep_archivo/generar`, cuyo efecto se clasifica `EFECTO` (`:428-438`). Los alias `/api/v3/vep/carga` y `/api/v3/vep_archivo/carga` están en `services/central-api/src/central_api/api/bot_compat.py:105-106`.

`services/central-api/src/central_api/input_files.py:20-23` traduce la referencia pública de archivo a ruta local; `:47-53` descarga la referencia y `:64-71` limita a 7 MB. `services/central-api/src/central_api/security/credentials.py:85-103,227-263` puede mover aliases CUIT/subida antes del schema. Cuando el alias de subida ya fue convertido a `subir_pdf`, se elimina el antiguo antes de que `_unificar_subida` vea ambos.

## Normalización, salida y efectos

- TXT UTF-8 estricto, no vacío, hasta 7.000.000 bytes y validación de 1 cabecera + 1–600 registros (`schema.py:20`, `plugin.py:223-232`). El plugin vuelve a verificar existencia/extensión/tamaño/contenido antes del portal.
- `incluir_json=null` se elimina explícitamente en el validator `before`; queda default `true`. Lo mismo ocurre con `subir_pdf=null` y `eliminar_descargas=null`, que recuperan sus defaults (`schema.py:105-123`). La afirmación “null inválido” es falsa para estos campos.
- `representado_cuit` opcional toma CUIT de sesión cuando falta. Medios aceptados: `link`, `pago_mis_cuentas`, `internet_banking`, `xn_group`, `qr` (`schema.py:94-107,125-130`).
- `data` normal incluye operación, CUIT representado, medio, número de registros, lote y nombre PDF; al publicar suma huella/tamaño. Puede incluir nombre/huella del PNG QR en el medio `qr`. Con `incluir_json=false`, el objeto queda solo con `detalle`, aunque la generación del VEP sigue (`plugin.py:273-319`).
- El click de generación es un efecto fiscal. Los archivos temporales se eliminan siempre aunque `eliminar_descargas=false`; una falla al descargar/subir puede ocurrir después de generado el volante (`schema.py:82-131`, `plugin.py:267-319`).

## Discrepancias observadas

1. **Comentario de QR obsoleto.** `runtime/portals/vep_archivo.py:8-13` afirma que QR no está activo/no se admite, mientras schema acepta `qr` (`schema.py:18`) y plugin descarga el QR (`plugin.py:300-316`).
2. **`eliminar_descargas` es no-op observable.** La entrada acepta el booleano, pero el flujo de archivos del portal hace cleanup siempre (`schema.py:105-123`, `runtime/portals/vep_archivo.py:243-318`).
3. **Aliases contradictorios pueden ser descartados antes de validación.** El validador interno detecta diferencias en CUIT/subida si recibe ambos (`schema.py:28-43,64-80`), pero normalización central puede convertir y retirar alias antes de que los helpers los comparen (`credentials.py:85-103,227-263`).
4. **JSON Schema incompleto.** Requiere nombres internos de `archivo_path`, publica una lista de flags parcial y omite varios aliases/modelos (`schema.py:141-164`); la superficie pública se describe como `archivo_key` tras el mapeo de archivo (`input_files.py:20-23`).
5. **No idempotencia/ambigüedad de efecto.** Un error remoto tras click final no permite saber si el VEP fue creado (`plugin.py:267-319`, portal `:114-173`).

## Pruebas consultadas

`services/bot-worker/tests/test_portal_vep_archivo.py:49-129,131-205` cubre validación del lote y descargas QR; `services/bot-worker/tests/test_vep_archivo_plugin.py` revisa salida/efecto. No se ejecutaron portales.

## Referencias consultadas

- `services/bot-worker/src/bot_worker/bots/vep_archivo/schema.py:20-164`
- `services/bot-worker/src/bot_worker/bots/vep_archivo/plugin.py:141-319`
- `services/bot-worker/src/bot_worker/runtime/portals/vep_archivo.py:8-13,31-40,114-216,243-318`
- `services/central-api/src/central_api/api/bots.py:419-438`
- `services/central-api/src/central_api/api/bot_compat.py:105-106`
- `services/central-api/src/central_api/security/credentials.py:74-103,227-263`
- `services/central-api/src/central_api/input_files.py:20-23,47-53,64-71`
- `services/central-api/src/central_api/api/bot_payloads.py:298-303`
- `services/bot-worker/tests/test_portal_vep_archivo.py:49-129,131-205`; `services/bot-worker/tests/test_vep_archivo_plugin.py`
