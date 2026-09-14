-- ============================================================================
--  Consultas de práctica.
--
--  IMPORTANTE: este archivo vive en ejemplos/, NO en sql/. Es a propósito.
--  El Dockerfile solo copia sql/ a /docker-entrypoint-initdb.d/. Si estuviera
--  ahí, Postgres lo ejecutaría solo al crear la base.
--
--  Para correrlas: copiar y pegar dentro de psql, o en Adminer (localhost:8080).
-- ============================================================================


-- ---------------------------------------------------------------------------
-- 0. VERIFICACIÓN: ¿el esquema se creó bien?
-- ---------------------------------------------------------------------------

\dt                     -- 7 tablas
\dv                     -- 3 vistas
\d audio                -- estructura y restricciones

SELECT COUNT(*) AS etiquetas FROM etiqueta;        -- 13
SELECT * FROM almacenamiento;                      -- 1 fila: dónde vive todo
SELECT * FROM estacion;                            -- las estaciones registradas


-- ---------------------------------------------------------------------------
-- 1. LOS LINKS  ← lo que se pide más seguido
-- ---------------------------------------------------------------------------

-- El link de descarga directa de cada audio.
-- Ojo: el link NO está guardado en ninguna columna. Se construye al consultar,
-- juntando las piezas de almacenamiento con audio.ruta_relativa. Por eso
-- cambiar de carpeta o de cuenta es UPDATE de una fila, no de un millón.
SELECT audio_id, nombre_archivo, estacion, duracion_seg, tamano_mb, url_descarga
FROM v_audios
ORDER BY fecha_grabacion
LIMIT 20;

-- Solo los links, listos para pegar en un reproductor o pasarlos a un script
SELECT url_descarga FROM v_audios ORDER BY fecha_grabacion;

-- Link para abrir en el navegador (visor de SharePoint) en vez de descargar
SELECT nombre_archivo, url_ver FROM v_audios LIMIT 5;

-- Demostración de la decisión de diseño: cambiar TODOS los links de golpe.
-- (No ejecutar en serio a menos que de verdad se mueva la carpeta.)
--   UPDATE almacenamiento
--   SET raiz_servidor = '/personal/<cuenta>/Documents/<carpeta nueva>'
--   WHERE origen = 'onedrive-cabalas';
-- Después de eso, v_audios devuelve todos los links ya corregidos.


-- ---------------------------------------------------------------------------
-- 2. METADATOS: duración, peso, formato
-- ---------------------------------------------------------------------------

-- Cuánto audio hay y cuánto pesa
SELECT
    COUNT(*)                                            AS n_audios,
    ROUND(SUM(duracion_seg) / 3600.0, 2)                AS horas,
    ROUND(SUM(tamano_bytes) / 1073741824.0, 2)          AS gigabytes,
    ROUND(AVG(duracion_seg), 1)                         AS duracion_media_seg,
    MIN(fecha_grabacion)                                AS primera,
    MAX(fecha_grabacion)                                AS ultima
FROM audio;

-- Por estación
SELECT es.nombre AS estacion,
       COUNT(a.id)                                      AS n_audios,
       ROUND(SUM(a.duracion_seg) / 3600.0, 2)           AS horas,
       ROUND(SUM(a.tamano_bytes) / 1048576.0, 1)        AS megabytes
FROM estacion es
LEFT JOIN fuente f ON f.estacion_id = es.id
LEFT JOIN audio  a ON a.fuente_id   = f.id
GROUP BY es.nombre
ORDER BY es.nombre;

-- Desglose técnico: sirve para detectar archivos grabados con otra
-- configuración. Importa porque Wav2Vec2 espera 16 kHz.
SELECT formato, codec, sample_rate, canales,
       COUNT(*)                                         AS n,
       ROUND(SUM(tamano_bytes) / 1048576.0, 1)          AS megabytes
FROM audio
GROUP BY formato, codec, sample_rate, canales
ORDER BY n DESC;

-- Audios sospechosos: muy cortos (posible archivo truncado)
SELECT id, nombre_archivo, duracion_seg, tamano_bytes
FROM audio WHERE duracion_seg < 1 ORDER BY duracion_seg;

-- Audios sin fecha: el nombre no siguió el formato DD_MM_AAAA_HH_MM_SS
SELECT id, nombre_archivo FROM audio WHERE fecha_grabacion IS NULL;


-- ---------------------------------------------------------------------------
-- 3. SENSORES
-- ---------------------------------------------------------------------------

SELECT medido_en, humedad_pct, temperatura_c, nombre_archivo
FROM lectura_sensor
ORDER BY medido_en
LIMIT 20;

-- Resumen ambiental por día
SELECT DATE(medido_en AT TIME ZONE 'America/Bogota')    AS dia,
       COUNT(*)                                         AS n_lecturas,
       ROUND(AVG(humedad_pct), 1)                       AS humedad_media,
       MIN(humedad_pct)                                 AS humedad_min,
       MAX(humedad_pct)                                 AS humedad_max,
       ROUND(AVG(temperatura_c), 1)                     AS temp_media
FROM lectura_sensor
GROUP BY dia
ORDER BY dia;

-- Ciclo diario: a qué hora hace más frío y hay más humedad
SELECT EXTRACT(HOUR FROM medido_en AT TIME ZONE 'America/Bogota') AS hora,
       ROUND(AVG(humedad_pct), 1)                       AS humedad_media,
       ROUND(AVG(temperatura_c), 1)                     AS temp_media,
       COUNT(*)                                         AS n
FROM lectura_sensor
GROUP BY hora
ORDER BY hora;

-- Huecos en el registro: si el sensor dejó de reportar, se ve aquí.
-- LAG() mira la fila anterior; la diferencia dice cuánto tiempo pasó.
SELECT medido_en,
       medido_en - LAG(medido_en) OVER (PARTITION BY estacion_id ORDER BY medido_en)
                                                        AS desde_la_anterior
FROM lectura_sensor
ORDER BY medido_en;


-- ---------------------------------------------------------------------------
-- 4. EL CRUCE AUDIO ↔ SENSOR  (la consulta de la sustentación)
-- ---------------------------------------------------------------------------
--  El sensor y el micrófono no miden al mismo ritmo, así que no sirve un JOIN
--  por igualdad de fecha. v_audio_ambiente busca, para cada audio, la lectura
--  más cercana en el tiempo de SU misma estación.

SELECT audio_id, nombre_archivo, fecha_grabacion,
       humedad_pct, temperatura_c, desfase_seg
FROM v_audio_ambiente
ORDER BY fecha_grabacion
LIMIT 20;

-- Solo los cruces confiables: lectura a menos de 10 minutos del audio.
-- Si el desfase son horas, el dato ambiental no describe ese momento.
SELECT audio_id, nombre_archivo, humedad_pct, temperatura_c, desfase_seg
FROM v_audio_ambiente
WHERE desfase_seg <= 600
ORDER BY humedad_pct DESC;

-- CONTROL DE CALIDAD DE ETIQUETAS
-- El modelo dijo "lluvia" pero el higrómetro marcaba poca humedad: candidatos
-- a falso positivo. Es evidencia física independiente del audio, y sale gratis
-- por tener las dos cosas en la misma base.
SELECT amb.audio_id, amb.nombre_archivo, amb.humedad_pct,
       ROUND(an.confianza * 100, 1)                     AS confianza_modelo
FROM v_audio_ambiente amb
JOIN anotacion an ON an.audio_id = amb.audio_id AND an.origen_anotacion = 'modelo'
JOIN etiqueta  e  ON e.id = an.etiqueta_id AND e.nombre = 'lluvia'
WHERE amb.desfase_seg <= 600
  AND amb.humedad_pct < 70
ORDER BY an.confianza DESC;

-- Y al revés: humedad muy alta pero nadie etiquetó lluvia. Cola de revisión.
SELECT amb.audio_id, amb.nombre_archivo, amb.humedad_pct, v.etiquetas
FROM v_audio_ambiente amb
JOIN v_labels v ON v.audio_id = amb.audio_id
WHERE amb.desfase_seg <= 600
  AND amb.humedad_pct > 90
  AND NOT ('lluvia' = ANY(v.etiquetas))
ORDER BY amb.humedad_pct DESC;


-- ---------------------------------------------------------------------------
-- 5. LA VISTA v_labels — lo que consume el entrenamiento
-- ---------------------------------------------------------------------------

SELECT * FROM v_labels LIMIT 10;

-- Conjunto de entrenamiento, con link para descargar cada archivo
SELECT audio_id, url_descarga, etiquetas
FROM v_labels
WHERE particion = 'train' AND CARDINALITY(etiquetas) > 0;

-- Cola de trabajo: audios que nadie ha etiquetado todavía
SELECT audio_id, nombre_archivo, duracion_seg, url_descarga
FROM v_labels
WHERE CARDINALITY(etiquetas) = 0
ORDER BY audio_id;


-- ---------------------------------------------------------------------------
-- 6. BALANCE DE CLASES
-- ---------------------------------------------------------------------------
--  Un dataset desbalanceado explica por qué un modelo "acierta el 80%" sin
--  haber aprendido nada.

SELECT e.nombre                                         AS clase,
       e.categoria,
       COUNT(DISTINCT an.audio_id)                      AS n_audios,
       ROUND(100.0 * COUNT(DISTINCT an.audio_id)
             / NULLIF((SELECT COUNT(*) FROM audio), 0), 1) AS porcentaje
FROM etiqueta e
LEFT JOIN anotacion an ON an.etiqueta_id = e.id
GROUP BY e.nombre, e.categoria
ORDER BY n_audios DESC;

-- Balance por partición: train, val y test deben parecerse entre sí
SELECT v.particion, e_nombre AS clase, COUNT(*) AS n
FROM v_labels v, UNNEST(v.etiquetas) AS e_nombre
GROUP BY v.particion, e_nombre
ORDER BY e_nombre, v.particion;


-- ---------------------------------------------------------------------------
-- 7. REPARTIR TRAIN / VAL / TEST
-- ---------------------------------------------------------------------------
--  La partición se asigna POR FUENTE (estación + día), nunca por audio: así los
--  clips de una misma sesión no se reparten entre train y test.
--  Ejemplo — 70/15/15 al azar sobre las fuentes:

-- UPDATE fuente SET particion = sub.p
-- FROM (
--     SELECT id,
--            CASE WHEN r < 0.70 THEN 'train'
--                 WHEN r < 0.85 THEN 'val'
--                 ELSE 'test' END AS p
--     FROM (SELECT id, random() AS r FROM fuente) x
-- ) sub
-- WHERE fuente.id = sub.id;

SELECT particion, COUNT(*) AS n_fuentes FROM fuente GROUP BY particion;


-- ---------------------------------------------------------------------------
-- 8. INTEGRIDAD
-- ---------------------------------------------------------------------------

-- Audios sin fuente: no tendrían partición, así que no se pueden entrenar
SELECT id, nombre_archivo FROM audio WHERE fuente_id IS NULL;

-- Comprobación de que sha256 bloquea duplicados.
-- DEBE devolver 0 filas siempre. Si devuelve algo, la restricción se cayó.
SELECT sha256, COUNT(*) FROM audio GROUP BY sha256 HAVING COUNT(*) > 1;

-- Lo mismo para sensores: una estación no puede tener dos lecturas del mismo
-- instante. También debe dar 0 filas.
SELECT estacion_id, medido_en, COUNT(*)
FROM lectura_sensor GROUP BY estacion_id, medido_en HAVING COUNT(*) > 1;
