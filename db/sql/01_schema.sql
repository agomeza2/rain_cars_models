-- ============================================================================
--  Proyecto: Producción Sonido — semillero CABALAS
--  Base de datos de METADATOS de audio y sensores.
--  Los archivos (.wav y .txt) viven en OneDrive/SharePoint; aquí solo va la
--  información sobre ellos y el link para llegar a cada uno.
--
--  Este archivo se ejecuta UNA SOLA VEZ, automáticamente, la primera vez que
--  el contenedor crea su volumen de datos. Ver DOCKER.md, sección 3.
-- ============================================================================


-- ----------------------------------------------------------------------------
--  0. Helper: escapar una ruta para que sirva dentro de una URL
-- ----------------------------------------------------------------------------
--  SharePoint recibe la ruta del archivo como un PARÁMETRO de la URL (todo lo
--  que va después del "?"). Dentro de un parámetro no vale ningún carácter
--  especial sin escapar — ni siquiera la barra: si va como "/" literal, el
--  servidor corta la lectura del parámetro ahí y responde 404.
--
--  Esta función hace percent-encoding completo: deja pasar solo los caracteres
--  seguros y convierte todo lo demás a sus bytes UTF-8 en hexadecimal.
--      '/Documents/Estación 1/x.wav'
--      -> '%2FDocuments%2FEstaci%C3%B3n%201%2Fx.wav'
-- ----------------------------------------------------------------------------
CREATE FUNCTION url_encode(p TEXT) RETURNS TEXT
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


-- ----------------------------------------------------------------------------
--  1. ALMACENAMIENTO — dónde viven los archivos
-- ----------------------------------------------------------------------------
--  DECISIÓN DE DISEÑO: la dirección de la nube está en UNA fila de esta tabla,
--  no repetida en cada uno de los miles de audios.
--
--  Si el link completo se guardara en cada fila de `audio`, el día que cambie
--  la cuenta, el tenant o la carpeta habría que reescribir la tabla entera.
--  Así se hace un UPDATE de una fila y todos los links quedan corregidos,
--  porque el link no se *guarda*: se *calcula* (ver la vista v_audios).
--
--  `origen` es un nombre lógico ('onedrive-cabalas'), no una URL. Es lo que
--  queda escrito en audio.origen y en lectura_sensor.origen.
-- ----------------------------------------------------------------------------
CREATE TABLE almacenamiento (
    origen          TEXT        PRIMARY KEY,
    tipo            TEXT        NOT NULL DEFAULT 'sharepoint'
                                CHECK (tipo IN ('sharepoint','onedrive','local','azure_blob')),

    -- La dirección, partida en tres piezas. Guardarla armada no sirve: cada
    -- forma de link (ver, descargar, reproducir) la recompone distinto.
    host            TEXT        NOT NULL,   -- https://<tenant>-my.sharepoint.com
    sitio           TEXT        NOT NULL,   -- /personal/<cuenta>
    raiz_servidor   TEXT        NOT NULL,   -- /personal/<cuenta>/Documents/<carpeta raíz>

    descripcion     TEXT,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE almacenamiento IS
    'Traduce el nombre lógico de un almacenamiento a su dirección real. Una fila por nube.';


-- ----------------------------------------------------------------------------
--  2. ESTACION — el punto físico de monitoreo en la finca
-- ----------------------------------------------------------------------------
--  "Estación 1" es una carpeta hoy, pero es una entidad real: un micrófono con
--  su sensor de temperatura y humedad, instalado en un sitio. Cuando aparezca
--  la Estación 2 no hay que cambiar nada: es un INSERT.
-- ----------------------------------------------------------------------------
CREATE TABLE estacion (
    id              SMALLINT    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo          TEXT        NOT NULL UNIQUE,   -- 'EST1'  (se usa en los códigos de fuente)
    nombre          TEXT        NOT NULL UNIQUE,   -- 'Estación 1' (el nombre de la carpeta)
    finca           TEXT,
    latitud         NUMERIC(9,6),
    longitud        NUMERIC(9,6),
    instalada_en    DATE,
    descripcion     TEXT
);


-- ----------------------------------------------------------------------------
--  3. FUENTE — la sesión de grabación
-- ----------------------------------------------------------------------------
--  Agrupa los clips que salieron de la misma estación el mismo día.
--
--  DECISIÓN DE DISEÑO: la columna `particion` (train/val/test) vive AQUÍ, no en
--  `audio`. Si viviera en `audio`, dos clips de la misma noche y el mismo
--  micrófono podrían caer uno en train y otro en test; el modelo memorizaría el
--  ruido de fondo de esa estación en esa noche y la métrica de test saldría
--  inflada. Eso es *data leakage*. Al ponerla en `fuente`, la partición se
--  decide una vez por sesión y todos sus clips la heredan.
-- ----------------------------------------------------------------------------
CREATE TABLE fuente (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    estacion_id     SMALLINT    REFERENCES estacion(id) ON DELETE RESTRICT,

    codigo          TEXT        NOT NULL UNIQUE,   -- 'EST1-2026-08-01'
    fecha           DATE,
    dispositivo     TEXT,

    particion       TEXT        NOT NULL DEFAULT 'train'
                                CHECK (particion IN ('train','val','test')),

    notas           TEXT,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_fuente_estacion ON fuente (estacion_id);

COMMENT ON COLUMN fuente.particion IS
    'train/val/test a nivel de sesión, para evitar data leakage entre clips hermanos.';


-- ----------------------------------------------------------------------------
--  4. AUDIO — cada archivo .wav
-- ----------------------------------------------------------------------------
--  DECISIÓN DE DISEÑO 1: el audio NO se guarda dentro de Postgres. Un WAV de
--  15 min a 44.1 kHz pesa ~150 MB; como BYTEA infla el backup, encarece
--  cualquier SELECT y obliga a extraerlo de la base para poder oírlo.
--
--  DECISIÓN DE DISEÑO 2: la ubicación va partida en `origen` + `ruta_relativa`.
--  La ruta es relativa a la carpeta raíz del proyecto en la nube, no la ruta de
--  Windows: en tu equipo la carpeta sincronizada está en un sitio y en el de tu
--  compañero en otro, y la misma fila tiene que servir en los dos.
--
--  DECISIÓN DE DISEÑO 3: `sha256` es UNIQUE. Es la huella del contenido. Si el
--  mismo audio se vuelve a subir con otro nombre, el INSERT falla y el duplicado
--  no entra. Un duplicado repartido entre train y test vuelve a ser leakage.
-- ----------------------------------------------------------------------------
CREATE TABLE audio (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fuente_id       BIGINT      REFERENCES fuente(id) ON DELETE RESTRICT,

    -- --- identidad y ubicación --------------------------------------------
    nombre_archivo  TEXT        NOT NULL,
    origen          TEXT        NOT NULL REFERENCES almacenamiento(origen),
    ruta_relativa   TEXT        NOT NULL,
    url_onedrive    TEXT,   -- link directo de OneDrive para abrir el archivo (lo llena ingesta.py)
    item_id         TEXT,                      -- id estable de OneDrive (Graph), si algún día se usa
    sha256          CHAR(64)    NOT NULL UNIQUE,

    -- --- metadatos técnicos ------------------------------------------------
    tamano_bytes    BIGINT      NOT NULL CHECK (tamano_bytes > 0),
    duracion_seg    NUMERIC(10,3) NOT NULL CHECK (duracion_seg > 0),
    formato         TEXT,
    codec           TEXT,
    sample_rate     INTEGER     CHECK (sample_rate > 0),
    canales         SMALLINT    CHECK (canales > 0),
    bitrate_kbps    INTEGER,

    -- --- ubicación en el tiempo -------------------------------------------
    -- Sale del NOMBRE del archivo (01_08_2026_00_06_05.wav), no de la fecha de
    -- modificación del sistema: OneDrive reescribe esa fecha al sincronizar.
    fecha_grabacion TIMESTAMPTZ,
    inicio_en_fuente_seg NUMERIC(10,3) CHECK (inicio_en_fuente_seg >= 0),

    ingerido_en     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (origen, ruta_relativa)
);

CREATE INDEX idx_audio_fuente ON audio (fuente_id);
CREATE INDEX idx_audio_fecha  ON audio (fecha_grabacion);

COMMENT ON COLUMN audio.sha256          IS 'Huella del contenido. UNIQUE: bloquea duplicados aunque cambie el nombre.';
COMMENT ON COLUMN audio.tamano_bytes    IS 'Peso del archivo en bytes.';
COMMENT ON COLUMN audio.duracion_seg    IS 'Duración en segundos, leída con ffprobe al ingerir.';
COMMENT ON COLUMN audio.fecha_grabacion IS 'Extraída del nombre del archivo; la fecha del sistema no es confiable tras sincronizar.';
COMMENT ON COLUMN audio.url_onedrive    IS 'Link directo de OneDrive (onedrive.aspx?id=...) calculado por ingesta.py desde `almacenamiento`. NULL si falta la fila de almacenamiento.';


-- ----------------------------------------------------------------------------
--  5. LECTURA_SENSOR — cada archivo .txt del sensor
-- ----------------------------------------------------------------------------
--  Formato de los .txt: fecha_hora, humedad, temperatura
--      01_08_2026_00_06_05 , 58.83 , 18.51
--       DD_MM_AAAA_HH_MM_SS   %HR     °C
--
--  DECISIÓN DE DISEÑO: los sensores van en su PROPIA tabla, no en columnas de
--  `audio`. Razones concretas:
--    - las cadencias no coinciden: el sensor mide cada X minutos y el
--      micrófono graba cuando graba; no hay un uno-a-uno;
--    - hay lecturas sin audio y audios sin lectura exacta;
--    - si mañana se agrega un sensor de presión o de luz, es una columna aquí,
--      no una migración de la tabla de audios.
--  El cruce entre ambos se hace por tiempo en la vista v_audio_ambiente.
--
--  UNIQUE (estacion_id, medido_en): la misma estación no puede tener dos
--  lecturas para el mismo instante. Es lo que hace que re-ingerir sea seguro.
-- ----------------------------------------------------------------------------
CREATE TABLE lectura_sensor (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    estacion_id     SMALLINT    NOT NULL REFERENCES estacion(id) ON DELETE RESTRICT,

    medido_en       TIMESTAMPTZ NOT NULL,
    humedad_pct     NUMERIC(5,2) CHECK (humedad_pct >= 0 AND humedad_pct <= 100),
    temperatura_c   NUMERIC(5,2) CHECK (temperatura_c > -50 AND temperatura_c < 80),

    -- de qué archivo salió, por si hay que auditar una lectura rara
    origen          TEXT        NOT NULL REFERENCES almacenamiento(origen),
    ruta_relativa   TEXT        NOT NULL,
    nombre_archivo  TEXT        NOT NULL,
    url_onedrive    TEXT,   -- link directo de OneDrive del .txt (lo llena ingesta.py)

    ingerido_en     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (estacion_id, medido_en)
);

CREATE INDEX idx_lectura_tiempo   ON lectura_sensor (estacion_id, medido_en);
CREATE INDEX idx_lectura_archivo  ON lectura_sensor (ruta_relativa);

COMMENT ON COLUMN lectura_sensor.url_onedrive IS
    'Link directo de OneDrive del .txt del que salió la lectura. Se calcula igual que audio.url_onedrive.';


-- ----------------------------------------------------------------------------
--  6. ETIQUETA — catálogo de clases
-- ----------------------------------------------------------------------------
--  DECISIÓN DE DISEÑO: las clases son FILAS, no columnas booleanas
--  (`es_lluvia`, `es_carro`, ...). Agregar "motosierra" es un INSERT de una
--  línea: sin ALTER TABLE, sin migración, sin tocar el código que consulta.
-- ----------------------------------------------------------------------------
CREATE TABLE etiqueta (
    id              SMALLINT    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre          TEXT        NOT NULL UNIQUE,
    categoria       TEXT        CHECK (categoria IN ('biofonia','antropofonia','geofonia','otro')),
    descripcion     TEXT,
    activa          BOOLEAN     NOT NULL DEFAULT TRUE
);

COMMENT ON COLUMN etiqueta.categoria IS
    'Ecoacústica: biofonía (fauna), antropofonía (humanos/máquinas), geofonía (lluvia, viento).';


-- ----------------------------------------------------------------------------
--  7. ANOTACION — "este audio, en este tramo, contiene esta clase"
-- ----------------------------------------------------------------------------
--  Tabla puente N:M a propósito: un mismo clip puede tener lluvia Y carro al
--  tiempo, que es lo que pasa en campo.
--
--  Guarda además QUIÉN lo dijo: una etiqueta puesta por una persona no vale lo
--  mismo que una predicha por el modelo. Con `origen_anotacion` se puede
--  entrenar solo con lo humano y usar lo del modelo para revisión.
-- ----------------------------------------------------------------------------
CREATE TABLE anotacion (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    audio_id        BIGINT      NOT NULL REFERENCES audio(id)    ON DELETE CASCADE,
    etiqueta_id     SMALLINT    NOT NULL REFERENCES etiqueta(id) ON DELETE RESTRICT,

    inicio_seg      NUMERIC(10,3) NOT NULL DEFAULT 0 CHECK (inicio_seg >= 0),
    fin_seg         NUMERIC(10,3) NOT NULL           CHECK (fin_seg   >  0),

    confianza       NUMERIC(5,4) CHECK (confianza >= 0 AND confianza <= 1),

    origen_anotacion TEXT       NOT NULL DEFAULT 'humano'
                                CHECK (origen_anotacion IN ('humano','modelo')),
    modelo          TEXT,       -- 'WAV2VEC2 v1'; NULL si es humano
    anotador        TEXT,       -- quién la puso; NULL si es modelo

    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (fin_seg > inicio_seg)
);

CREATE INDEX idx_anotacion_audio    ON anotacion (audio_id);
CREATE INDEX idx_anotacion_etiqueta ON anotacion (etiqueta_id);


-- ============================================================================
--  VISTAS
-- ============================================================================

-- ----------------------------------------------------------------------------
--  v_audios — el catálogo con el LINK ya armado
-- ----------------------------------------------------------------------------
--  Aquí es donde se paga la decisión de guardar la ubicación partida: el link
--  no está almacenado en ninguna fila, se construye al consultar. Cambiar de
--  carpeta o de cuenta = UPDATE de una fila en `almacenamiento`.
-- ----------------------------------------------------------------------------
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
    al.host || al.sitio || '/_layouts/15/onedrive.aspx?id=' || r.enc        AS url_ver,
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
    'Catálogo de audios con el link de descarga calculado. Es lo que se consulta para oír un archivo.';


-- ----------------------------------------------------------------------------
--  v_labels — lo que consume el dataloader de PyTorch
-- ----------------------------------------------------------------------------
--  Una fila por audio, con sus clases agregadas, la partición heredada de la
--  fuente y el link para descargarlo. El script de entrenamiento consulta esto
--  y no necesita saber nada de la estructura interna: si mañana se parte una
--  tabla en dos, se reescribe la vista y el entrenamiento no se entera.
-- ----------------------------------------------------------------------------
CREATE VIEW v_labels AS
SELECT
    a.id                                          AS audio_id,
    a.nombre_archivo,
    a.origen,
    a.ruta_relativa,
    al.host || al.sitio || '/_layouts/15/download.aspx?SourceUrl='
        || url_encode(al.raiz_servidor || '/' || a.ruta_relativa)  AS url_descarga,
    a.duracion_seg,
    a.tamano_bytes,
    a.sample_rate,
    es.nombre                                     AS estacion,
    f.codigo                                      AS fuente,
    f.particion,
    COALESCE(
        ARRAY_AGG(DISTINCT e.nombre ORDER BY e.nombre)
            FILTER (WHERE e.nombre IS NOT NULL),
        ARRAY[]::TEXT[]
    )                                             AS etiquetas,
    COUNT(an.id)                                  AS n_anotaciones
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


-- ----------------------------------------------------------------------------
--  v_audio_ambiente — a cada audio, la lectura de sensor más cercana en tiempo
-- ----------------------------------------------------------------------------
--  Esta es la vista que le da sentido a tener los dos tipos de archivo en la
--  misma base. El sensor y el micrófono no miden al mismo ritmo, así que no
--  sirve un JOIN por igualdad: para cada audio se busca la lectura de SU misma
--  estación con la menor diferencia de tiempo (eso hace el LATERAL).
--
--  Para qué sirve: la humedad alta es evidencia independiente de lluvia. Si el
--  modelo dice "lluvia" y el higrómetro marcaba 95%, hay confirmación cruzada;
--  si marcaba 40%, esa predicción hay que revisarla. Es control de calidad de
--  etiquetas sin tener que escuchar todo a mano.
--
--  `desfase_seg` dice qué tan lejos quedó la lectura: si son horas, el dato
--  ambiental no aplica y hay que descartarlo.
-- ----------------------------------------------------------------------------
CREATE VIEW v_audio_ambiente AS
SELECT
    a.id                                          AS audio_id,
    a.nombre_archivo,
    es.nombre                                     AS estacion,
    a.fecha_grabacion,
    s.medido_en                                   AS sensor_medido_en,
    s.humedad_pct,
    s.temperatura_c,
    ROUND(ABS(EXTRACT(EPOCH FROM (s.medido_en - a.fecha_grabacion)))::NUMERIC, 0) AS desfase_seg
FROM audio a
LEFT JOIN fuente   f  ON f.id  = a.fuente_id
LEFT JOIN estacion es ON es.id = f.estacion_id
LEFT JOIN LATERAL (
    SELECT ls.medido_en, ls.humedad_pct, ls.temperatura_c
    FROM lectura_sensor ls
    WHERE ls.estacion_id = f.estacion_id
    ORDER BY ABS(EXTRACT(EPOCH FROM (ls.medido_en - a.fecha_grabacion)))
    LIMIT 1
) s ON a.fecha_grabacion IS NOT NULL;

COMMENT ON VIEW v_audio_ambiente IS
    'Cruza cada audio con la lectura de sensor más cercana de su estación. desfase_seg indica si el dato aplica.';
