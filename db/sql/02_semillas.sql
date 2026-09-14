-- ============================================================================
--  Datos iniciales (semillas).
--
--  Va en un archivo aparte de 01_schema.sql a propósito: la ESTRUCTURA y los
--  DATOS cambian por razones distintas y a ritmos distintos. Los archivos de
--  /docker-entrypoint-initdb.d/ se ejecutan en orden alfabético, por eso el
--  prefijo numérico.
-- ============================================================================


-- ----------------------------------------------------------------------------
--  Dónde viven los archivos
-- ----------------------------------------------------------------------------
--  Estas dos URL se descomponen así:
--
--    https://academiausbbogedu-my.sharepoint.com      <- el sitio (tenant de la USB)
--    /personal/adguevarav_academia_usbbog_edu_co      <- el OneDrive de Alvaro
--    /Documents                                       <- la biblioteca de documentos
--    /Tesis-Semillero/AudiosSemilleroCABALAS          <- la carpeta raíz del proyecto
--
--  Lo que sigue después de eso ("Estación 1/xxx.wav") es lo que se guarda en
--  audio.ruta_relativa. Por eso la raíz llega hasta AudiosSemilleroCABALAS y no
--  hasta Estación 1: así la Estación 2 entra sin tocar esta fila.
--
--  ⚠️ "Documents" va en INGLÉS aunque el OneDrive se vea en español y diga
--     "Documentos". Ese es el nombre interno de la biblioteca; el español es
--     solo la etiqueta que muestra la interfaz.
--
--  ⚠️ VERIFICA LA RUTA. Abre la carpeta en OneDrive web y mira la barra de
--     direcciones: trae ...onedrive.aspx?id=%2Fpersonal%2F...%2FDocuments%2F...
--     Ese valor de `id`, decodificado (%2F es "/" y %20 es un espacio), es
--     exactamente la ruta que va aquí.
--
--  Se guardan las TRES piezas por separado, no las URL ya armadas: cada forma
--  de link (abrir, descargar, reproducir) las recompone distinto, y la vista
--  v_audios las junta y codifica al consultar.
--
--  ⚠️ Si cambia la cuenta, la carpeta o el tenant: se hace UPDATE de ESTA fila
--     y todos los links de la base quedan corregidos. No hay que tocar ni una
--     sola fila de `audio`. Ese es justamente el punto del diseño.
--
--     UPDATE almacenamiento
--     SET host          = 'https://<tenant>-my.sharepoint.com',
--         sitio         = '/personal/<cuenta>',
--         raiz_servidor = '/personal/<cuenta>/Documents/<carpeta raíz>'
--     WHERE origen = 'onedrive-cabalas';
-- ----------------------------------------------------------------------------
INSERT INTO almacenamiento (origen, tipo, host, sitio, raiz_servidor, descripcion) VALUES
(
    'onedrive-cabalas',
    'sharepoint',
    'https://academiausbbogedu-my.sharepoint.com',
    '/personal/adguevarav_academia_usbbog_edu_co',
    '/personal/adguevarav_academia_usbbog_edu_co/Documents/Tesis-Semillero/AudiosSemilleroCABALAS',
    'OneDrive institucional USB Bogotá (adguevarav) — Tesis-Semillero/AudiosSemilleroCABALAS'
);


-- ----------------------------------------------------------------------------
--  Estaciones de monitoreo
-- ----------------------------------------------------------------------------
--  `nombre` tiene que coincidir EXACTAMENTE con el nombre de la carpeta en
--  OneDrive: es así como ingesta.py sabe a qué estación pertenece cada archivo.
--  Cuando instalen la Estación 2, se agrega una línea aquí y se corre la
--  ingesta; no hay nada más que cambiar.
-- ----------------------------------------------------------------------------
INSERT INTO estacion (codigo, nombre, finca, descripcion) VALUES
    ('EST1', 'Estación 1', NULL, 'Micrófono + sensor de temperatura y humedad'),
    ('ESTB', 'Estación bosque', NULL, 'Estación en el bosque');


-- ----------------------------------------------------------------------------
--  Catálogo de clases
-- ----------------------------------------------------------------------------
--  Agregar una clase al proyecto = agregar una línea aquí (y un INSERT en la
--  base que ya está corriendo). No hay migración de esquema.
-- ----------------------------------------------------------------------------
INSERT INTO etiqueta (nombre, categoria, descripcion) VALUES
    ('lluvia',     'geofonia',     'Precipitación sobre vegetación, techo o suelo'),
    ('viento',     'geofonia',     'Viento en el micrófono o en la vegetación'),
    ('trueno',     'geofonia',     'Descarga eléctrica'),

    ('ave',        'biofonia',     'Canto o llamado de ave'),
    ('insecto',    'biofonia',     'Grillos, chicharras, coros de insectos'),
    ('anfibio',    'biofonia',     'Ranas y sapos'),
    ('mamifero',   'biofonia',     'Vocalización de mamífero'),

    ('carro',      'antropofonia', 'Vehículo liviano en movimiento'),
    ('motor',      'antropofonia', 'Motor estacionario, moto, guadaña, bomba'),
    ('persona',    'antropofonia', 'Voz humana'),
    ('motosierra', 'antropofonia', 'Motosierra — indicador de tala'),

    ('silencio',   'otro',         'Fondo sin evento acústico relevante'),
    ('otro',       'otro',         'Evento no clasificable en las clases anteriores');
