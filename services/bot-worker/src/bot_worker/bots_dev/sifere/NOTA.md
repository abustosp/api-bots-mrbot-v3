# sifere - nota dev (stub)

Port V3 de `api-bots-mrbot-v2/app/bot/sifere_bot.py` (`bot_sifere`).

- Operación: `consultar` (retenciones por jurisdicción 901–924 y
  periodo AAAAMM; sin `jurisdicciones` se consultan todas).
- Sin pandas en el worker: el resumen por jurisdicción viaja como JSON
  plano y los CSV crudos quedan como artefactos.
- `payload_ejemplo.json` usa `subir: false` y dos jurisdicciones:
  valida esquema sin navegador ni subida.
- Referencia V2: `api-bots-mrbot-v2/app/bot/sifere_bot.py` y
  `api-bots-mrbot-v2/bots_dev/sifere/`.
- Pendiente de integración: métodos del servicio
  (`seleccionar_representado`, `consultar_jurisdiccion`) los provee la
  sesión de navegador del runtime.
