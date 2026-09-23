# retper_iibb_agip - nota dev (stub)

Port V3 de `api-bots-mrbot-v2/app/bot/retper_iibb_agip_bot.py`.

- Operación: `consultar` (retenciones/percepciones AGIP, rango AAAAMM).
- El usuario ClaveCiudad viaja en la sección sellada (`agip_usuario`);
  sin sección se usa el CUIT de las credenciales fiscales.
- `payload_ejemplo.json` usa `subir_archivo: false`: valida esquema y
  nomenclatura sin navegador ni subida.
- Referencia V2: `api-bots-mrbot-v2/app/bot/retper_iibb_agip_bot.py` y
  `api-bots-mrbot-v2/bots_dev/retenciones_agip/`.
- Pendiente de integración: métodos del servicio (`ingresar`,
  `consultar_retper`) los provee la sesión de navegador del runtime.
