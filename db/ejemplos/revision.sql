-- ============================================================================
--  REVISIÓN — ¿quedó todo cargado y bien?
--
--  Se corre de una sola vez, desde la carpeta db:
--      docker compose exec -T db psql -U audios_admin -d audios < ejemplos\revision.sql
--
--  Cada bloque dice qué deberías ver. Lo que este archivo NO puede comprobar es
--  si los archivos siguen en tu disco (SQL no ve tu carpeta): eso lo hace
--      python ingesta.py --verificar
-- ============================================================================

\pset border 2
\timing off


\echo ''
\echo '=== 1. CONTEOS GENERALES ============================================='
\echo '    Compara "audios" con los .wav que tienes en la carpeta.'
\echo ''

SELECT
    (SELECT COUNT(*) FROM audio)                        AS audios,
    (SELECT COUNT(*) FROM lectura_sensor)               AS lecturas_sensor,
    (SELECT COUNT(DISTINCT ruta_relativa) FROM lectura_sensor) AS archivos_txt,
    (SELECT COUNT(*) FROM fuente)                       AS sesiones,
    (SELECT COUNT(*) FROM estacion)                     AS estaciones,
    (SELECT COUNT(*) FROM anotacion)                    AS anotaciones;


\echo ''
\echo '=== 2. POR ESTACIÓN =================================================='
\echo '    Si una estación aparece con 0 audios, su carpeta no se ingirió.'
\echo ''

SELECT
    es.nombre                                           AS estacion,
    COUNT(DISTINCT f.id)                                AS sesiones,
    COUNT(a.id)                                         AS audios,
    ROUND(SUM(a.duracion_seg) / 3600.0, 2)              AS horas,
    ROUND(SUM(a.tamano_bytes) / 1073741824.0, 2)        AS gigabytes,
    MIN(a.fecha_grabacion)::date                        AS desde,
    MAX(a.fecha_grabacion)::date                        AS hasta
FROM estacion es
LEFT JOIN fuente f ON f.estacion_id = es.id
LEFT JOIN audio  a ON a.fuente_id   = f.id
GROUP BY es.nombre
ORDER BY es.nombre;


\echo ''
\echo '=== 3. ¿ALGÚN AUDIO QUEDÓ SIN METADATOS? ============================='
\echo '    Todas las columnas deben dar 0. Si no, ffprobe falló en ese archivo.'
\echo ''

SELECT
    COUNT(*) FILTER (WHERE duracion_seg   IS NULL)      AS sin_duracion,
    COUNT(*) FILTER (WHERE tamano_bytes   IS NULL)      AS sin_peso,
    COUNT(*) FILTER (WHERE fecha_grabacion IS NULL)     AS sin_fecha,
    COUNT(*) FILTER (WHERE sample_rate    IS NULL)      AS sin_sample_rate,
    COUNT(*) FILTER (WHERE formato        IS NULL)      AS sin_formato,
    COUNT(*) FILTER (WHERE fuente_id      IS NULL)      AS sin_sesion,
    COUNT(*) FILTER (WHERE ruta_relativa  IS NULL
                        OR ruta_relativa = '')          AS sin_ruta
FROM audio;


\echo ''
\echo '=== 4. FICHA COMPLETA DE UN AUDIO ===================================='
\echo '    Revisa que la ruta y el link apunten a donde deben.'
\echo ''

\x on
SELECT
    audio_id, nombre_archivo, estacion, fuente, particion,
    fecha_grabacion, duracion_seg, tamano_mb, formato, codec,
    sample_rate, canales, origen, ruta_relativa, url_descarga
FROM v_audios
ORDER BY fecha_grabacion
LIMIT 1;
\x off


\echo ''
\echo '=== 5. LAS RUTAS Y LOS LINKS ========================================='
\echo '    Primeros y últimos. La ruta debe empezar por la carpeta de estación.'
\echo ''

SELECT audio_id, ruta_relativa, LEFT(url_descarga, 78) || '...' AS link
FROM v_audios
ORDER BY fecha_grabacion
LIMIT 3;

SELECT audio_id, ruta_relativa
FROM v_audios
ORDER BY fecha_grabacion DESC
LIMIT 3;


\echo ''
\echo '=== 6. DATOS DE LOS SENSORES ========================================='
\echo '    Rango de humedad y temperatura por día. Valores fuera de lo posible'
\echo '    ya los rechaza la base, así que aquí solo miras que sean creíbles.'
\echo ''

SELECT
    (medido_en AT TIME ZONE 'America/Bogota')::date      AS dia,
    COUNT(*)                                            AS lecturas,
    ROUND(MIN(humedad_pct), 1)                          AS hum_min,
    ROUND(AVG(humedad_pct), 1)                          AS hum_media,
    ROUND(MAX(humedad_pct), 1)                          AS hum_max,
    ROUND(MIN(temperatura_c), 1)                        AS temp_min,
    ROUND(AVG(temperatura_c), 1)                        AS temp_media,
    ROUND(MAX(temperatura_c), 1)                        AS temp_max
FROM lectura_sensor
GROUP BY dia
ORDER BY dia;


\echo ''
\echo '=== 7. CRUCE AUDIO <-> SENSOR ========================================'
\echo '    Cuántos audios tienen una lectura ambiental cerca (< 10 min).'
\echo '    Los "sin_dato" son audios sin ninguna lectura de su estación.'
\echo ''

SELECT
    COUNT(*)                                            AS audios,
    COUNT(*) FILTER (WHERE desfase_seg <= 600)          AS con_sensor_cercano,
    COUNT(*) FILTER (WHERE desfase_seg >  600)          AS sensor_lejano,
    COUNT(*) FILTER (WHERE desfase_seg IS NULL)         AS sin_dato,
    ROUND(AVG(desfase_seg) FILTER (WHERE desfase_seg <= 600), 0) AS desfase_medio_seg
FROM v_audio_ambiente;


\echo ''
\echo '=== 8. INTEGRIDAD ===================================================='
\echo '    Las tres consultas deben devolver CERO filas.'
\echo ''

\echo '-- audios con el mismo contenido (sha256 repetido):'
SELECT sha256, COUNT(*) FROM audio GROUP BY sha256 HAVING COUNT(*) > 1;

\echo '-- dos lecturas de sensor para el mismo instante y estación:'
SELECT estacion_id, medido_en, COUNT(*)
FROM lectura_sensor GROUP BY estacion_id, medido_en HAVING COUNT(*) > 1;

\echo '-- audios cuyo origen no existe en la tabla almacenamiento:'
SELECT a.id, a.origen FROM audio a
LEFT JOIN almacenamiento al ON al.origen = a.origen
WHERE al.origen IS NULL;


\echo ''
\echo '=== 9. DÓNDE VIVEN LOS ARCHIVOS ======================================'
\echo ''

\x on
SELECT origen, tipo, host, sitio, raiz_servidor FROM almacenamiento;
\x off

\echo ''
\echo '=== FIN =============================================================='
\echo ''
