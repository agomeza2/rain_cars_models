-- ============================================================================
--  MIGRACIÓN 02 — columna url_onedrive en audio y lectura_sensor
--
--  Se aplica sobre una base YA CARGADA, sin borrar nada y sin volver a ingerir.
--  Desde la carpeta db:
--      docker compose exec -T db psql -U audios_admin -d audios < migraciones\02_url_columnas.sql
--
--  ---------------------------------------------------------------------------
--  QUÉ HACE
--
--  Agrega `url_onedrive` a las dos tablas de archivos (audio = .wav y
--  lectura_sensor = .txt) y rellena las filas que ya existían con el link
--  directo de OneDrive, construido igual que la vista v_audios: con las tres
--  piezas de `almacenamiento` (host + sitio + raiz_servidor) y la ruta relativa.
--
--  Las filas NUEVAS no se rellenan aquí: las llena ingesta.py al correr. Si se
--  corre esta migración ANTES de la ingesta, la ingesta escribe el link al
--  insertar. Si se corre DESPUÉS, rellena lo que la ingesta dejó en NULL (por
--  ejemplo, si se ingirió con una versión vieja del script).
--
--  Por qué url_onedrive no se guardaba y ahora sí: es lo que pide el parquet.
--  La vista v_audios la calcula al consultar; ingesta.py ahora la guarda en la
--  fila para que el .parquet salga con una columna url_onedrive lista para usar.
-- ============================================================================

BEGIN;

ALTER TABLE audio          ADD COLUMN IF NOT EXISTS url_onedrive TEXT;
ALTER TABLE lectura_sensor ADD COLUMN IF NOT EXISTS url_onedrive TEXT;

-- ---------------------------------------------------------------------------
--  Relleno retroactivo: mismas piezas que usa v_audios.
--  url_encode() es la función del esquema (percent-encoding, / -> %2F).
-- ---------------------------------------------------------------------------
UPDATE audio a
SET url_onedrive = al.host || al.sitio || '/_layouts/15/onedrive.aspx?id='
                   || url_encode(al.raiz_servidor || '/' || a.ruta_relativa)
FROM almacenamiento al
WHERE al.origen = a.origen
  AND a.url_onedrive IS NULL;

UPDATE lectura_sensor ls
SET url_onedrive = al.host || al.sitio || '/_layouts/15/onedrive.aspx?id='
                   || url_encode(al.raiz_servidor || '/' || ls.ruta_relativa)
FROM almacenamiento al
WHERE al.origen = ls.origen
  AND ls.url_onedrive IS NULL;

COMMIT;

\echo ''
\echo '=== Migración aplicada. Check: ==='
SELECT 'audio'            AS tabla, COUNT(*) FILTER (WHERE url_onedrive IS NULL) AS sin_url,
       COUNT(*) AS total FROM audio
UNION ALL
SELECT 'lectura_sensor'   AS tabla, COUNT(*) FILTER (WHERE url_onedrive IS NULL) AS sin_url,
       COUNT(*) AS total FROM lectura_sensor;