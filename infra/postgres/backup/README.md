# postgres/backup

Rutina de respaldo de PostgreSQL 17. Diseno en
[plan de infra](../../../plans/06-infra/plan.md) seccion 9.4 y secuencia de
release en [despliegue-central.md](../../deploy/runbooks/despliegue-central.md).

| Archivo | Proposito |
|---|---|
| `backup.sh` | `pg_dump --format=custom` mas checksum SHA-256 y poda local |
| `restore-test.sh` | Restaura en un postgres efimero y verifica tablas canonicas |

## Politica inicial

| Aspecto | Valor |
|---|---|
| Metodo | `pg_dump --format=custom` con usuario de lectura |
| Frecuencia | Diario a las 02:00 UTC y previo a toda migracion |
| Destino | Object storage separado, cifrado y con acceso minimo |
| Retencion | 30 diarios, 12 mensuales y 4 anuales |
| Integridad | Checksum SHA-256 por objeto, verificado en origen y destino |
| Restore test | Mensual en instancia aislada, con duracion reportada |
| RPO inicial | Maximo 24 horas, salvo backup previo a release |
| RTO objetivo | 4 horas, medidas en el restore test |

El backup no incluye secretos de runtime. Los artefactos tienen su propia
lifecycle policy en el bucket.

## Uso

```bash
./infra/postgres/backup/backup.sh ./backups
```

El DSN sale de `DATABASE_URL` o del secreto `database_url` montado por el
compose. Tras el `OK`, subir el `.dump` y su `.sha256` al bucket y verificar
el checksum en destino. Solo entonces se ejecuta el job one-off `migrate`.

```bash
./infra/postgres/backup/restore-test.sh ./backups/mrbot-20260919T020000Z.dump
```

La prueba usa una imagen `postgres:17` efimera y nunca toca produccion.
Si el conteo de tablas canonicas (`users`, `jobs`, `workers`, `plans`,
`subscriptions`) falla, el respaldo se declara invalido y se repite.
