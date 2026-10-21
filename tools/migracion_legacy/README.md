# Migracion V1/V2 a V3

Script: `migrar_a_v3.py` (orquestador local) + `loader_central.py` (corre dentro de `mrbot-v3-central-api-1`).

```bash
python3 tools/migracion_legacy/migrar_a_v3.py stage          # DBs/bkp_local_v3.db
python3 tools/migracion_legacy/migrar_a_v3.py dump-central   # DBs/central-v3-dump.db
python3 tools/migracion_legacy/migrar_a_v3.py rehearsal      # ensayo en clon, no toca la base real
python3 tools/migracion_legacy/migrar_a_v3.py apply --yes --send-emails
python3 tools/migracion_legacy/migrar_a_v3.py token --yes
```

La carga es una unica transaccion (todo o nada) y hace antes un `pg_dump` en `~/backups` del servidor.
Es idempotente por usuario (mail) y por job (UUIDv5 de origen). Los correos se envian despues del commit.
`dump-central` vuelca TODAS las tablas completas de la central (todas las columnas) y, para depurar, agrega junto al ciphertext su texto plano: `jobs.credential_plaintext` y `api_keys.api_key_plaintext` (descifrados dentro de la central con su clave RSA; NULL si no descifra). `--sin-claves-en-claro` desactiva el texto plano. El dump resultante es secreto: no versionar ni compartir.
El backup del panel admin (`POST /admin/backups/dump`) ya incluye todas las tablas y columnas, con las credenciales cifradas tal cual.
`bkp_local_v3.db` contiene claves en claro de V1: no versionar ni compartir.
