-- ============================================================================
--  MIGRACIÓN 01 — arreglar la forma de los links
--
--  Se aplica sobre una base YA CARGADA, sin borrar nada y sin volver a ingerir.
--  Desde la carpeta db:
--      docker compose exec -T db psql -U audios_admin -d audios < migraciones\01_links_encoding.sql
--
--  ---------------------------------------------------------------------------
--  EL PROBLEMA
--
--  Los links daban 404. La ruta estaba bien; lo que estaba mal era la
--  codificación. SharePoint recibe la ruta del archivo COMO UN PARÁMETRO de la
--  URL (todo lo que va después del "?"), y dentro de un parámetro las barras
--  tienen que ir escritas %2F. Si van como "/" literales, el servidor corta la
--  lectura del parámetro donde encuentra la primera y busca un archivo que no
--  existe.
--
--  Mal:   ?SourceUrl=/personal/adguevarav.../Estaci%C3%B3n%201/audio.wav
--  Bien:  ?id=%2Fpersonal%2Fadguevarav...%2FEstaci%C3%B3n%201%2Faudio.wav
--
--  La segunda forma es la que usa el propio OneDrive cuando copias la barra de
--  direcciones. Ahora la base genera esa.
--
--  ---------------------------------------------------------------------------
--  POR QUÉ SE ARREGLA CON UNA MIGRACIÓN Y NO REINGIRIENDO
--
--  Ninguna fila de `audio` guarda un link: guardan `ruta_relativa`, y el link
--  se calcula al consultar. Así que corregir la forma del link es cambiar UNA
--  función y DOS vistas. Los 145 audios y sus 2.160 lecturas no se tocan.
--  Si el link estuviera escrito en cada fila, esto sería volver a leer 16 GB.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
--  1. Codificador completo: escapa TODO menos los caracteres seguros.
--     A diferencia de url_ruta(), este también convierte "/" en %2F, que es
--     lo que hace falta dentro de un parámetro de URL.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION url_encode(p TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT AS $fn$
    SELECT COALESCE(string_agg(
        CASE
            WHEN t.ch ~ '^[A-Za-z0-9._~-]$' THEN t.ch
            ELSE (
                SELECT string_agg('%' || upper(substring(x.h FROM i FOR 2)), '')
                FROM (SELECT encode(convert_to(t.ch, 'UTF8'), 'hex') AS h) x,
                     generate_series(1, length(x.h), 2) AS i
            )
        END, '' ORDER BY t.ord), '')
    FROM regexp_split_to_table(p, '') WITH ORDINALITY AS t(ch, ord);
$fn$;

COMMENT ON FUNCTION url_encode(TEXT) IS
    'Percent-encoding completo, incluida la barra (/ -> %2F). Para valores dentro de una URL.';


-- ---------------------------------------------------------------------------
--  2. La tabla `almacenamiento` ahora guarda las tres piezas por separado,
--     en vez de dos URL ya armadas. Así se puede construir cualquier forma de
--     link sin volver a tocar nada.
--       host          -> https://academiausbbogedu-my.sharepoint.com
--       sitio         -> /personal/adguevarav_academia_usbbog_edu_co
--       raiz_servidor -> /personal/.../Documents/Tesis-Semillero/AudiosSemilleroCABALAS
-- ---------------------------------------------------------------------------
ALTER TABLE almacenamiento
    ADD COLUMN IF NOT EXISTS host          TEXT,
    ADD COLUMN IF NOT EXISTS sitio         TEXT,
    ADD COLUMN IF NOT EXISTS raiz_servidor TEXT;

UPDATE almacenamiento SET
    host          = 'https://academiausbbogedu-my.sharepoint.com',
    sitio         = '/personal/adguevarav_academia_usbbog_edu_co',
    raiz_servidor = '/personal/adguevarav_academia_usbbog_edu_co/Documents/Tesis-Semillero/AudiosSemilleroCABALAS'
WHERE origen = 'onedrive-cabalas';


-- ---------------------------------------------------------------------------
--  3. Vistas nuevas. Se borran y se recrean porque cambian columnas; no
--     contienen datos, solo la forma de consultarlos.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_labels;
DROP VIEW IF EXISTS v_audios;

CREATE VIEW v_audios AS
SELECT
    a.id                                        AS audio_id,
    a.nombre_archivo,
    es.nombre                                   AS estacion,
    f.codigo                                    AS fuente,
    f.particion,
    a.fecha_grabacion,
    a.duracion_seg,
    a.tamano_bytes,
    ROUND(a.tamano_bytes / 1048576.0, 2)        AS tamano_mb,
    a.formato, a.codec, a.sample_rate, a.canales,
    -- abrir en OneDrive (visor / reproductor)
    al.host || al.sitio || '/_layouts/15/onedrive.aspx?id=' || r.enc      AS url_ver,
    -- descarga directa del archivo
    al.host || al.sitio || '/_layouts/15/download.aspx?SourceUrl=' || r.enc AS url_descarga,
    r.ruta_servidor,
    a.origen,
    a.ruta_relativa,
    a.sha256
FROM audio a
JOIN almacenamiento al ON al.origen = a.origen
LEFT JOIN fuente   f  ON f.id  = a.fuente_id
LEFT JOIN estacion es ON es.id = f.estacion_id
CROSS JOIN LATERAL (
    SELECT p AS ruta_servidor, url_encode(p) AS enc
    FROM (SELECT al.raiz_servidor || '/' || a.ruta_relativa AS p) q
) r;

COMMENT ON VIEW v_audios IS
    'Catálogo de audios con los links calculados. Cambiar de carpeta o de cuenta = UPDATE de una fila en almacenamiento.';

CREATE VIEW v_labels AS
SELECT
    a.id                                        AS audio_id,
    a.nombre_archivo,
    a.origen,
    a.ruta_relativa,
    al.host || al.sitio || '/_layouts/15/download.aspx?SourceUrl='
        || url_encode(al.raiz_servidor || '/' || a.ruta_relativa)  AS url_descarga,
    a.duracion_seg,
    a.tamano_bytes,
    a.sample_rate,
    es.nombre                                   AS estacion,
    f.codigo                                    AS fuente,
    f.particion,
    COALESCE(
        ARRAY_AGG(DISTINCT e.nombre ORDER BY e.nombre)
            FILTER (WHERE e.nombre IS NOT NULL),
        ARRAY[]::TEXT[]
    )                                           AS etiquetas,
    COUNT(an.id)                                AS n_anotaciones
FROM audio a
JOIN almacenamiento al ON al.origen = a.origen
LEFT JOIN fuente    f  ON f.id  = a.fuente_id
LEFT JOIN estacion  es ON es.id = f.estacion_id
LEFT JOIN anotacion an ON an.audio_id = a.id
LEFT JOIN etiqueta  e  ON e.id  = an.etiqueta_id
GROUP BY a.id, a.nombre_archivo, a.origen, a.ruta_relativa,
         al.host, al.sitio, al.raiz_servidor,
         a.duracion_seg, a.tamano_bytes, a.sample_rate,
         es.nombre, f.codigo, f.particion;

COMMENT ON VIEW v_labels IS
    'Vista plana audio + etiquetas + partición + link. Contrato estable para el pipeline de ML.';

COMMIT;

\echo ''
\echo '=== Migración aplicada. Un link de ejemplo: ==='
\x on
SELECT audio_id, nombre_archivo, url_ver, url_descarga FROM v_audios ORDER BY audio_id LIMIT 1;
\x off
