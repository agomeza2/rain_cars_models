# =============================================================================
#  Imagen de la base de datos del proyecto: audios-db
#
#  ¿Por qué una imagen propia en vez de usar `postgres:16` pelado?
#
#  Porque el esquema queda HORNEADO dentro de la imagen. Cualquier persona del
#  semillero hace `docker compose up` en su máquina y obtiene exactamente la
#  misma base, con las mismas tablas, sin tener que acordarse de correr un
#  script a mano. La estructura de la base deja de ser un paso manual y pasa a
#  ser parte del artefacto versionado en git.
#
#  Cómo funciona: la imagen oficial de Postgres ejecuta TODO archivo .sql o .sh
#  que encuentre en /docker-entrypoint-initdb.d/ la primera vez que inicializa
#  su directorio de datos. Solo la primera vez: si el volumen ya tiene datos,
#  los ignora (así no te borra la base en cada reinicio).
# =============================================================================

FROM postgres:16

# Fija el juego de caracteres, para que las tildes y la ñ no se rompan.
ENV LANG=C.UTF-8
ENV POSTGRES_INITDB_ARGS="--encoding=UTF8"

# Solo se copia sql/. Los archivos de ejemplos/ quedan FUERA a propósito:
# si estuvieran aquí, Postgres los ejecutaría al crear la base.
COPY sql/ /docker-entrypoint-initdb.d/
