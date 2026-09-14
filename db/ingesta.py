"""
============================================================================
 Ingesta  —  proyecto Producción Sonido / semillero CABALAS
============================================================================

 Qué hace
 --------
 Recorre la carpeta del proyecto sincronizada desde OneDrive y registra en
 PostgreSQL todo lo que encuentra:

   .WAV  -> tabla `audio`
            nombre, peso en bytes, duración, formato, códec, sample rate,
            canales, bitrate, sha256, la fecha sacada del nombre del archivo
            y la URL directa de OneDrive para abrir/descargar el archivo.

.TXT  -> tabla `lectura_sensor`
            fecha_hora, humedad (%) y temperatura (°C) del sensor del micrófono.

 Lo que NO hace: copiar, mover, subir ni borrar archivos. Los archivos están en
 OneDrive y ahí se quedan; esto solo los cataloga. El link a cada uno SÍ se
 guarda: en `audio.url_onedrive` (para los .wav) y en `lectura_sensor.url_onedrive`
 (para los .txt), calculado al insertar con las piezas de la tabla
 `almacenamiento` (host + sitio + raiz_servidor). Da igual en qué equipo corra:
 el link se arma con los datos de la base, no con rutas de Windows.

 Al terminar una ingesta real, el script vuelca las DOS tablas a sendos
 archivos .parquet -- `audio.parquet` y `sensores.parquet` -- para compartir
 el catálogo completo (cada fila trae su url_onedrive) sin dar acceso directo
 a la base. Con una barra de progreso para ver el avance mientras lee los
 archivos en la carpeta local.


 Cinco decisiones que valen para la sustentación
 -----------------------------------------------

  1. SE GUARDA LA RUTA RELATIVA *Y* LA URL DIRECTA, NO LA RUTA DE WINDOWS.
     En tu equipo la carpeta sincronizada está en un sitio y en el de tu
     compañero en otro, así que `ruta_relativa` se sigue guardando
     ('Estación 1/01_08_2026_00_06_05.wav') para que cada quien la resuelva
     contra su propia copia local si hace falta. Pero además, al insertar, se
     arma la URL completa con la tabla `almacenamiento` (la direccion real
     de SharePoint vive ahí, en una sola fila) y se guarda en `url_onedrive`,
     lista para abrir sin volver a calcular nada. Como sale de la misma fila
     que usa la vista v_audios, los links guardados y los calculados coinciden
     siempre. Para corregirlos todos de golpe se actualiza ESA fila, no las
     miles de `audio`.

 2. LA FECHA SALE DEL NOMBRE DEL ARCHIVO, NO DEL SISTEMA DE ARCHIVOS.
    OneDrive reescribe la fecha de modificación al sincronizar, así que
    `st_mtime` diría "hoy" para una grabación de agosto. El nombre
    01_08_2026_00_06_05.wav sí es el dato real de campo.
    El formato es DD_MM_AAAA_HH_MM_SS (día primero, como en Colombia). Se
    confirma contra los CSV del repo: '01-08-2026.csv' y
    '2026-08-01-05-35-48.csv' son la misma fecha.

 3. EL SHA256 ES LA IDENTIDAD DEL AUDIO, NO EL NOMBRE.
    Los nombres se repiten y se cambian; el contenido no. La columna es UNIQUE:
    si el mismo audio se sube dos veces con nombres distintos, el segundo no
    entra. Importa porque un duplicado repartido entre train y test inflaría la
    métrica del modelo.

 4. LOS SENSORES SE DEDUPLICAN POR (estación, instante), NO POR ARCHIVO.
    Dos .txt distintos pueden traer la misma lectura. Lo que no puede repetirse
    es la medición: una estación no tuvo dos humedades diferentes a la misma
    hora. Por eso el UNIQUE de la tabla es (estacion_id, medido_en) y aquí se
    usa ON CONFLICT DO NOTHING.

 5. CADA ARCHIVO SE INSERTA COMPLETO O NO SE INSERTA.
    Cada uno va en su propia transacción. Si ffprobe falla a mitad de camino o
    se cae la conexión, no queda una fuente huérfana ni un audio sin metadatos,
    y el resto de la corrida sigue. Es la "A" de ACID: atomicidad.


 Uso
 ---
pip install -r requirements.txt
    python ingesta.py --dry-run          # muestra qué haría, sin escribir nada
    python ingesta.py                    # ingesta de verdad (al final exporta los 2 parquet)
    python ingesta.py --solo audio       # o --solo sensores
    python ingesta.py --links            # imprime los links de lo que ya está en la base
    python ingesta.py --parquet          # solo exporta los parquet (audio + sensores) y sale
    python ingesta.py --sin-parquet      # ingesta real pero sin exportar al final

 Requiere ffprobe en el PATH (viene con ffmpeg).
 ============================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg
from dotenv import load_dotenv
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

load_dotenv()

EXT_AUDIO  = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
EXT_SENSOR = {".txt"}

# Trozo de lectura para el hash: 1 MB. Ni tanto que se coma la RAM con archivos
# de 150 MB, ni tan poco que haga miles de llamadas al disco.
CHUNK = 1024 * 1024

# 01_08_2026_00_06_05  ->  día, mes, año, hora, minuto, segundo
RE_FECHA = re.compile(r"(\d{2})[_\-.](\d{2})[_\-.](\d{4})[_\-.](\d{2})[_\-.](\d{2})[_\-.](\d{2})")

# Un número decimal con punto o coma: 58.83, 18,51, -3.2
RE_NUMERO = re.compile(r"[-+]?\d+(?:[.,]\d+)?")

TZ = ZoneInfo("America/Bogota")


def buscar_carpetas_onedrive() -> list[Path]:
    """
    Busca en el perfil del usuario las carpetas que OneDrive pudo haber creado.

    OneDrive nombra la carpeta según la cuenta:
        OneDrive                                  -> cuenta personal (Hotmail/Outlook)
        OneDrive - <nombre de la organización>    -> cuenta institucional
    Si solo aparece la primera, la cuenta de la universidad no está
    sincronizando en este equipo, que es la causa más común del error de abajo.
    """
    try:
        return sorted(p for p in Path.home().iterdir()
                      if p.is_dir() and p.name.lower().startswith("onedrive"))
    except OSError:
        return []


def buscar_carpeta_proyecto(base: Path, profundidad: int = 3) -> list[Path]:
    """Dentro de una carpeta de OneDrive, busca algo que parezca la del proyecto."""
    pistas = ("audios", "cabalas", "semillero", "estacion", "estación")
    encontradas = []
    try:
        for p in base.rglob("*"):
            if not p.is_dir():
                continue
            if len(p.relative_to(base).parts) > profundidad:
                continue
            if any(x in p.name.lower() for x in pistas):
                encontradas.append(p)
    except OSError:
        pass
    return encontradas[:10]


def _diagnostico_carpeta() -> str:
    """Mensaje de ayuda cuando CARPETA_PROYECTO no sirve: dice qué SÍ hay."""
    partes = []
    onedrives = buscar_carpetas_onedrive()

    if not onedrives:
        partes.append(
            "No encontré ninguna carpeta de OneDrive en tu perfil de usuario.\n"
            "Instala/inicia sesión en la app de OneDrive con la cuenta de la "
            "universidad y sincroniza la carpeta de los audios."
        )
    else:
        partes.append("Carpetas de OneDrive que sí existen en este equipo:")
        for od in onedrives:
            institucional = " - " in od.name
            etiqueta = "institucional" if institucional else "cuenta personal"
            partes.append(f"    {od}    ({etiqueta})")

        if not any(" - " in od.name for od in onedrives):
            partes.append(
                "\nNinguna es institucional: solo está sincronizada tu cuenta personal.\n"
                "Los audios están en el OneDrive de la universidad, así que hay que\n"
                "agregar esa cuenta en la app de OneDrive (Configuración > Cuenta >\n"
                "Agregar una cuenta) y sincronizar la carpeta. Cuando eso pase\n"
                "aparecerá una carpeta nueva llamada 'OneDrive - <universidad>'."
            )

        for od in onedrives:
            for cand in buscar_carpeta_proyecto(od):
                partes.append(f"\n  ¿Será esta?  CARPETA_PROYECTO={cand}")

    return "\n".join(partes)


def config() -> dict:
    carpeta = os.getenv("CARPETA_PROYECTO", "").strip().strip('"')
    if not carpeta:
        sys.exit(
            "ERROR: falta CARPETA_PROYECTO en .env\n\n"
            + _diagnostico_carpeta()
        )

    raiz = Path(carpeta).expanduser()
    if not raiz.is_dir():
        sys.exit(
            f"ERROR: esta carpeta no existe en este equipo:\n  {raiz}\n\n"
            "El script lee archivos locales, no páginas web: la carpeta tiene que\n"
            "verse en el Explorador de Windows. Abrirla en el navegador no basta.\n\n"
            + _diagnostico_carpeta()
        )

    return {
        "raiz": raiz,
        "origen": os.getenv("ORIGEN", "onedrive-cabalas"),
        "parquet_ruta": Path(os.getenv("PARQUET_RUTA", "export/audio.parquet")),
        "parquet_sensores": Path(os.getenv("PARQUET_SENSORES", "export/sensores.parquet")),
        "dsn": (
            f"host={os.getenv('PGHOST', 'localhost')} "
            f"port={os.getenv('PGPORT', '5433')} "
            f"dbname={os.getenv('POSTGRES_DB', 'audios')} "
            f"user={os.getenv('POSTGRES_USER', 'audios_admin')} "
            f"password={os.getenv('POSTGRES_PASSWORD', '')}"
        ),
    }


# ---------------------------------------------------------------------------
# Utilidades de lectura de archivos
# ---------------------------------------------------------------------------

def sha256_archivo(ruta: Path) -> str:
    """Huella del contenido, leyendo por trozos para no cargar el archivo entero."""
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def fecha_de_texto(texto: str) -> datetime | None:
    """
    Saca un instante de un texto con formato DD_MM_AAAA_HH_MM_SS.
    Sirve tanto para el nombre de un .wav como para el contenido de un .txt.
    Se marca en hora de Colombia; sin zona horaria, Postgres asumiría UTC y
    todas las grabaciones quedarían corridas 5 horas.
    """
    m = RE_FECHA.search(texto)
    if not m:
        return None
    d, mes, a, h, mi, s = (int(g) for g in m.groups())
    try:
        return datetime(a, mes, d, h, mi, s, tzinfo=TZ)
    except ValueError:
        return None


def metadatos_ffprobe(ruta: Path) -> dict:
    """
    Datos técnicos del audio, según ffprobe.

    Se usa ffprobe y no una librería de Python porque es la misma herramienta
    que ya usa ffmpeg/minute.py en el pipeline de inferencia: un solo criterio
    para leer duración y sample rate en todo el proyecto.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        "-select_streams", "a:0",
        str(ruta),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe: {proc.stderr.decode(errors='ignore').strip()[:200]}")

    data = json.loads(proc.stdout)
    fmt, streams = data.get("format", {}), data.get("streams", [])
    if not streams:
        raise RuntimeError("el archivo no tiene pista de audio")
    st = streams[0]

    dur = fmt.get("duration") or st.get("duration")
    if dur is None:
        raise RuntimeError("ffprobe no reporta duración (¿archivo truncado?)")

    br = fmt.get("bit_rate")
    return {
        "duracion_seg": round(float(dur), 3),
        "formato": (fmt.get("format_name") or "").split(",")[0] or None,
        "codec": st.get("codec_name"),
        "sample_rate": int(st["sample_rate"]) if st.get("sample_rate") else None,
        "canales": int(st["channels"]) if st.get("channels") else None,
        "bitrate_kbps": int(int(br) / 1000) if br else None,
    }


def parsear_txt(ruta: Path) -> tuple[list[dict], str | None]:
    """
    Lee un .txt de sensor y devuelve (lecturas, motivo_si_no_hay_ninguna).

    Formato documentado:  fecha_hora , humedad , temperatura
        01_08_2026_00_06_05, 58.83, 18.51

    Cómo se parsea, y por qué así
    -----------------------------
    NO se lee línea por línea. Se busca cada aparición de una fecha en todo el
    texto, y se toman los dos primeros números que vengan DESPUÉS de ella y
    antes de la siguiente fecha. Así el mismo código lee las tres formas que
    puede escribir un datalogger, sin configurar nada:

        01_08_2026_00_06_05, 58.83, 18.51        (todo en una línea)
        01_08_2026_00_06_05\\n58.83\\n18.51        (un valor por línea)
        ...;58,83;18,51 ...;61,20;17,94          (varias lecturas seguidas)

    Es tolerante con el separador (coma, punto y coma, tabulación, espacio,
    salto de línea) y con la coma decimal, porque el formato exacto puede
    cambiar entre versiones del firmware y la ingesta no debería caerse por eso.
    Lo que NO es tolerante es el ORDEN: humedad primero, temperatura después,
    tal como está documentado. Si el sensor lo cambiara, se cambia aquí — a
    propósito y a la vista, no adivinando.
    """
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    marcas = list(RE_FECHA.finditer(texto))

    if not marcas:
        # Sin fecha no es un archivo de sensor: probablemente notas de campo.
        # No es un error, simplemente no es nuestro.
        return [], "sin fecha reconocible: no parece un archivo de sensor"

    lecturas: list[dict] = []
    descartadas = 0

    for i, m in enumerate(marcas):
        momento = fecha_de_texto(m.group(0))
        if momento is None:
            continue

        # El bloque que va desde el final de esta fecha hasta la siguiente.
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        numeros = [float(n.replace(",", "."))
                   for n in RE_NUMERO.findall(texto[m.end():fin])]
        if len(numeros) < 2:
            descartadas += 1
            continue

        humedad, temperatura = numeros[0], numeros[1]

        # Filtro de cordura: valores fuera del rango físico posible se descartan
        # en vez de contaminar la base. El CHECK de la tabla haría lo mismo,
        # pero abortando la transacción; aquí se descarta y se sigue.
        if not (0 <= humedad <= 100) or not (-50 < temperatura < 80):
            descartadas += 1
            continue

        lecturas.append({
            "medido_en": momento,
            "humedad_pct": round(humedad, 2),
            "temperatura_c": round(temperatura, 2),
        })

    if not lecturas:
        return [], (f"se encontraron {len(marcas)} fecha(s) pero ninguna con "
                    f"humedad y temperatura válidas")
    return lecturas, (f"{descartadas} lectura(s) descartadas" if descartadas else None)


def nombre_estacion(archivo: Path, raiz: Path) -> str | None:
    """
    A qué estación pertenece un archivo: la primera carpeta bajo la raíz.
        <raiz>/Estación 1/01_08_2026_00_06_05.wav   ->  'Estación 1'
    Ese nombre tiene que existir en la tabla `estacion` (ver 02_semillas.sql).
    """
    partes = archivo.relative_to(raiz).parts
    return partes[0] if len(partes) > 1 else None


def url_onedrive_de(al: dict, ruta_relativa: str) -> str | None:
    """
    Construye la URL directa de OneDrive/SharePoint para un archivo.

    `al` es la fila de `almacenamiento` (host, sitio, raiz_servidor): la misma
    que usa la vista v_audios. Así el link guardado y el calculado siempre
    coinciden, y si cambia la cuenta se corrige corrigiendo ESA fila, no las
    miles de registros.

    La ruta se url-encodea COMPLETA (incluidas las barras, / -> %2F), porque
    es un parámetro de la URL: 'Estación 1/x.wav' con tilde, espacio y barras
    crudas rompería el link.
    """
    if not (al.get("host") and al.get("sitio") and al.get("raiz_servidor")):
        return None
    ruta_servidor = f"{al['raiz_servidor']}/{ruta_relativa}"
    return f"{al['host']}{al['sitio']}/_layouts/15/onedrive.aspx?id={quote(ruta_servidor, safe='')}"


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

SQL_FUENTE = """
    INSERT INTO fuente (codigo, estacion_id, fecha, notas)
    VALUES (%(codigo)s, %(estacion_id)s, %(fecha)s, 'Creada automáticamente por ingesta.py')
    ON CONFLICT (codigo) DO UPDATE SET codigo = EXCLUDED.codigo
    RETURNING id;
"""
#  ^ el DO UPDATE que no cambia nada es un truco estándar: hace que RETURNING
#    devuelva el id tanto si la fila se creó como si ya existía (con DO NOTHING
#    no devolvería nada en el segundo caso).
#
#    Efecto secundario esperado: los ids de fuente pueden quedar con huecos
#    (1, 2, 4, ...). Postgres reserva el número de la secuencia ANTES de saber
#    si habrá conflicto y no lo devuelve. No es un error: una clave primaria
#    solo tiene que ser única, no consecutiva.

SQL_AUDIO = """
    INSERT INTO audio (
        fuente_id, nombre_archivo, origen, ruta_relativa, sha256,
        tamano_bytes, duracion_seg, formato, codec,
        sample_rate, canales, bitrate_kbps, fecha_grabacion, url_onedrive
    ) VALUES (
        %(fuente_id)s, %(nombre_archivo)s, %(origen)s, %(ruta_relativa)s, %(sha256)s,
        %(tamano_bytes)s, %(duracion_seg)s, %(formato)s, %(codec)s,
        %(sample_rate)s, %(canales)s, %(bitrate_kbps)s, %(fecha_grabacion)s, %(url_onedrive)s
    )
    RETURNING id;
"""

SQL_LECTURA = """
    INSERT INTO lectura_sensor (
        estacion_id, medido_en, humedad_pct, temperatura_c,
        origen, ruta_relativa, nombre_archivo, url_onedrive
    ) VALUES (
        %(estacion_id)s, %(medido_en)s, %(humedad_pct)s, %(temperatura_c)s,
        %(origen)s, %(ruta_relativa)s, %(nombre_archivo)s, %(url_onedrive)s
    )
    ON CONFLICT (estacion_id, medido_en) DO NOTHING
    RETURNING id;
"""


def cargar_estaciones(conn) -> dict[str, tuple[int, str]]:
    """
    nombre de carpeta -> (id, código corto)

    El nombre de la carpeta en OneDrive ('Estación 1') es lo que se ve en el
    Explorador; el código ('EST1') es lo que se usa para construir los códigos
    de fuente. Los dos viven en la tabla `estacion`: así el script no tiene que
    inventarse abreviaturas ni normalizar tildes por su cuenta.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT nombre, id, codigo FROM estacion;")
        return {n: (i, c) for n, i, c in cur.fetchall()}


def cargar_almacenamiento(conn) -> dict[str, dict]:
    """
    origen -> {host, sitio, raiz_servidor}

    La dirección REAL de cada nube vive en una sola fila de `almacenamiento`.
    De ahí se arma el link de cada archivo, igual que la vista v_audios. Si la
    fila no existe, los archivos quedan con url_onedrive = NULL.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT origen, host, sitio, raiz_servidor FROM almacenamiento;")
        return {o: {"host": h, "sitio": s, "raiz_servidor": r}
                for o, h, s, r in cur.fetchall()}


def asegurar_columnas_url(conn) -> None:
    """
    Crea `url_onedrive` en `audio` y `lectura_sensor` si el 01_schema.sql del
    proyecto todavía no la tiene. Idempotente: no hace nada si la columna ya
    existe. Así el script funciona aunque no se haya vuelto a correr el schema
    ni la migración 02 sobre la base.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE audio ADD COLUMN IF NOT EXISTS url_onedrive text;")
            cur.execute("ALTER TABLE lectura_sensor ADD COLUMN IF NOT EXISTS url_onedrive text;")


def hashes_existentes(conn) -> set[str]:
    """
    Trae de una sola vez los sha256 que ya están en la base.

    La alternativa mala sería un SELECT por archivo: con 5.000 audios serían
    5.000 idas y vueltas. Una consulta y un set en memoria resuelve igual. La
    restricción UNIQUE sigue ahí como red de seguridad real; esto solo evita
    calcular hashes en vano.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT sha256 FROM audio;")
        return {f[0] for f in cur.fetchall()}


# ---------------------------------------------------------------------------
# Procesos
# ---------------------------------------------------------------------------

def ingerir_audios(conn, cfg, raiz, archivos, estaciones, almacen) -> tuple[int, int, int]:
    nuevos = omitidos = fallidos = 0
    ya_estan = hashes_existentes(conn)
    al = almacen.get(cfg["origen"])
    if al and not (al["host"] and al["sitio"] and al["raiz_servidor"]):
        al = None  # la fila existe pero no sirve; los links quedan NULL
    print(f"  ({len(ya_estan)} audio(s) ya registrados en la base)\n")

    for archivo in tqdm(archivos, desc="Audios", unit="archivo", ncols=100, leave=True):
        rel = archivo.relative_to(raiz).as_posix()
        try:
            huella = sha256_archivo(archivo)
            if huella in ya_estan:
                omitidos += 1
                tqdm.write(f"  =  {rel}")
                continue

            est_nombre = nombre_estacion(archivo, raiz)
            if est_nombre not in estaciones:
                raise RuntimeError(
                    f"la estación '{est_nombre}' no está en la tabla `estacion`. "
                    f"Agrégala en 02_semillas.sql o con un INSERT."
                )
            est_id, est_codigo = estaciones[est_nombre]

            momento = fecha_de_texto(archivo.name)
            meta = metadatos_ffprobe(archivo)
            stat = archivo.stat()
            url = url_onedrive_de(al, rel)

            # La sesión de grabación agrupa por estación y día: EST1-2026-08-01.
            # Es lo que hereda la partición train/val/test.
            codigo_fuente = (
                f"{est_codigo}-{momento.date().isoformat()}" if momento
                else f"{est_codigo}-SIN-FECHA"
            )

            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(SQL_FUENTE, {
                        "codigo": codigo_fuente,
                        "estacion_id": est_id,
                        "fecha": momento.date() if momento else None,
                    })
                    fuente_id = cur.fetchone()[0]

                    cur.execute(SQL_AUDIO, {
                        "fuente_id": fuente_id,
                        "nombre_archivo": archivo.name,
                        "origen": cfg["origen"],
                        "ruta_relativa": rel,
                        "sha256": huella,
                        "tamano_bytes": stat.st_size,
                        "fecha_grabacion": momento,
                        "url_onedrive": url,
                        **meta,
                    })
                    audio_id = cur.fetchone()[0]

            ya_estan.add(huella)
            nuevos += 1
            tqdm.write(f"  +  {rel}  -> id={audio_id}  {meta['duracion_seg']}s  "
                       f"{stat.st_size / 1048576:.1f} MB  {meta['sample_rate']} Hz")
            if url is None:
                tqdm.write("       (sin url_onedrive: no hay fila en `almacenamiento`)")

        except psycopg.errors.UniqueViolation:
            omitidos += 1
            tqdm.write(f"  =  {rel}  (duplicado detectado por la base)")
        except Exception as e:
            fallidos += 1
            tqdm.write(f"  !  {rel}  -> {e}")

    return nuevos, omitidos, fallidos


def ingerir_sensores(conn, cfg, raiz, archivos, estaciones, almacen) -> tuple[int, int, int, int]:
    nuevas = omitidas = fallidos = ignorados = 0
    al = almacen.get(cfg["origen"])
    if al and not (al["host"] and al["sitio"] and al["raiz_servidor"]):
        al = None

    for archivo in tqdm(archivos, desc="Sensores", unit="archivo", ncols=100, leave=True):
        rel = archivo.relative_to(raiz).as_posix()
        try:
            est_nombre = nombre_estacion(archivo, raiz)
            if est_nombre not in estaciones:
                raise RuntimeError(f"la estación '{est_nombre}' no está en la tabla `estacion`")
            est_id, _ = estaciones[est_nombre]

            lecturas, aviso = parsear_txt(archivo)
            if not lecturas:
                # Un .txt sin fechas no es un error de la ingesta: es un archivo
                # que no es de sensor (notas de campo, por ejemplo).
                ignorados += 1
                tqdm.write(f"  ·  {rel}  ({aviso})")
                continue

            url = url_onedrive_de(al, rel)
            n_ins = 0
            with conn.transaction():
                with conn.cursor() as cur:
                    for lec in lecturas:
                        cur.execute(SQL_LECTURA, {
                            "estacion_id": est_id,
                            "origen": cfg["origen"],
                            "ruta_relativa": rel,
                            "nombre_archivo": archivo.name,
                            "url_onedrive": url,
                            **lec,
                        })
                        if cur.fetchone() is not None:
                            n_ins += 1

            nuevas += n_ins
            omitidas += len(lecturas) - n_ins
            marca = "+" if n_ins else "="
            tqdm.write(f"  {marca}  {rel}  -> {n_ins} nueva(s) de {len(lecturas)} lectura(s)"
                       + (f"  [{lecturas[0]['humedad_pct']}% HR, "
                          f"{lecturas[0]['temperatura_c']} °C]" if lecturas else ""))

        except Exception as e:
            fallidos += 1
            tqdm.write(f"  !  {rel}  -> {e}")

    return nuevas, omitidas, fallidos, ignorados


def verificar(conn, cfg, raiz: Path) -> int:
    """
    Comprueba que la copia local y lo que dice la base sigan siendo lo mismo.

    Por qué existe: la carpeta local y la de la nube son DOS copias. Si la
    carpeta está sincronizada por OneDrive se mantienen iguales solas; si no lo
    está (por ejemplo, se desvinculó la cuenta y quedó la carpeta suelta), se
    van separando sin que nadie se dé cuenta. Y el daño es silencioso: la fila
    tiene metadatos leídos de un archivo local que ya no existe, y un link que
    apunta a la nube. Ninguna de las dos partes falla sola.

    Esta función responde tres preguntas:
      1. ¿Cada audio de la base sigue teniendo su archivo local?
      2. ¿Hay archivos locales que nunca se ingirieron?
      3. ¿El archivo local cambió de tamaño desde que se registró?
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ruta_relativa, tamano_bytes FROM audio WHERE origen = %s "
            "ORDER BY ruta_relativa;", (cfg["origen"],))
        filas = cur.fetchall()

    print(f"Carpeta local : {raiz}")
    print(f"En la base    : {len(filas)} audio(s) con origen '{cfg['origen']}'\n")

    faltan, cambiados = [], []
    en_base = set()
    for aid, rel, bytes_db in filas:
        en_base.add(rel)
        local = raiz / rel
        if not local.exists():
            faltan.append((aid, rel))
        elif local.stat().st_size != bytes_db:
            cambiados.append((aid, rel, bytes_db, local.stat().st_size))

    locales = {p.relative_to(raiz).as_posix()
               for p in raiz.rglob("*")
               if p.is_file() and p.suffix.lower() in EXT_AUDIO and p.parent != raiz}
    sin_ingerir = sorted(locales - en_base)

    if faltan:
        print(f"✗ {len(faltan)} audio(s) están en la base pero NO en la carpeta local:")
        for aid, rel in faltan[:15]:
            print(f"    [{aid}] {rel}")
        if len(faltan) > 15:
            print(f"    ... y {len(faltan) - 15} más")
        print("  Puede ser que se movieran, se renombraran, o que CARPETA_PROYECTO")
        print("  esté apuntando a otra carpeta. El link de la nube puede seguir")
        print("  siendo válido: esto NO significa que el audio se perdió.\n")

    if cambiados:
        print(f"✗ {len(cambiados)} archivo(s) cambiaron de tamaño desde la ingesta:")
        for aid, rel, viejo, nuevo in cambiados[:15]:
            print(f"    [{aid}] {rel}   base={viejo} bytes, disco={nuevo} bytes")
        print("  El contenido ya no es el que se registró. Habría que volver a")
        print("  ingerirlos (borrar esas filas y correr la ingesta de nuevo).\n")

    if sin_ingerir:
        print(f"· {len(sin_ingerir)} archivo(s) locales que aún no están en la base:")
        for rel in sin_ingerir[:15]:
            print(f"    {rel}")
        if len(sin_ingerir) > 15:
            print(f"    ... y {len(sin_ingerir) - 15} más")
        print("  Corre `python ingesta.py` para agregarlos.\n")

    if not faltan and not cambiados and not sin_ingerir:
        print("✓ Todo cuadra: cada audio de la base tiene su archivo local, con el")
        print("  mismo tamaño, y no hay archivos locales pendientes de ingerir.")
        return 0
    return 1


def mostrar_links(conn, limite: int) -> None:
    """
    Imprime los links ya guardados en `audio.url_onedrive`. Ya no consulta la
    vista `v_audios`: la URL viene directo de la fila, tal como se guardó en
    la ingesta.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.id, a.nombre_archivo, e.nombre, a.duracion_seg, "
            "       round(a.tamano_bytes / 1048576.0, 1), a.url_onedrive "
            "FROM audio a "
            "JOIN fuente f ON f.id = a.fuente_id "
            "JOIN estacion e ON e.id = f.estacion_id "
            "ORDER BY a.fecha_grabacion NULLS LAST LIMIT %s;", (limite,)
        )
        filas = cur.fetchall()
    if not filas:
        print("La base no tiene audios todavía. Corre la ingesta primero.")
        return
    for aid, nombre, est, dur, mb, url in filas:
        print(f"\n[{aid}] {nombre}   {est}   {dur}s   {mb} MB\n     "
              f"{url or '(sin url_onedrive: falta la fila en `almacenamiento`)'}")


def _vuelca_parquet(conn, ruta_salida: Path, query: str) -> tuple[Path, int]:
    """Corre la consulta, vuelca el resultado a un .parquet y dice cuántas filas tenía."""
    with conn.cursor() as cur:
        cur.execute(query)
        columnas = [c.name for c in cur.description]
        filas = cur.fetchall()

    df = pd.DataFrame(filas, columns=columnas)
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ruta_salida, index=False)
    return ruta_salida, len(df)


def exportar_parquet(conn, ruta_audio: Path, ruta_sensores: Path) -> list[tuple[Path, int]]:
    """
    Vuelca las DOS tablas de archivos a sendos .parquet, cada fila con su
    url_onedrive de OneDrive ya resuelta:

      audio    -> ruta_audio     (catálogo de los .wav)
      sensores -> ruta_sensores  (lecturas sacadas de los .txt)

    Sirve para compartir el catálogo completo -- por ejemplo con el notebook
    de entrenamiento o para adjuntar a la sustentación -- sin dar acceso
    directo a la base de datos.
    """
    q_audio = """
        SELECT a.id, a.nombre_archivo, a.origen, a.ruta_relativa, a.url_onedrive,
               a.sha256, a.tamano_bytes, a.duracion_seg, a.formato, a.codec,
               a.sample_rate, a.canales, a.bitrate_kbps, a.fecha_grabacion,
               e.nombre AS estacion, f.codigo AS fuente_codigo
        FROM audio a
        JOIN fuente f ON f.id = a.fuente_id
        JOIN estacion e ON e.id = f.estacion_id
        ORDER BY a.id;
    """
    q_sensores = """
        SELECT ls.id, ls.medido_en, ls.humedad_pct, ls.temperatura_c,
               ls.origen, ls.ruta_relativa, ls.nombre_archivo, ls.url_onedrive,
               e.nombre AS estacion
        FROM lectura_sensor ls
        JOIN estacion e ON e.id = ls.estacion_id
        ORDER BY ls.id, ls.medido_en;
    """
    return [
        _vuelca_parquet(conn, ruta_audio, q_audio),
        _vuelca_parquet(conn, ruta_sensores, q_sensores),
    ]


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Cataloga los audios y las lecturas de sensor del semillero en PostgreSQL.")
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra qué haría, sin escribir en la base")
    ap.add_argument("--solo", choices=["audio", "sensores"],
                    help="procesa solo un tipo de archivo")
    ap.add_argument("--carpeta", metavar="RUTA",
                    help="sobrescribe CARPETA_PROYECTO solo para esta corrida")
    ap.add_argument("--links", action="store_true",
                    help="imprime los links de los audios ya registrados y sale")
    ap.add_argument("--verificar", action="store_true",
                    help="comprueba que la carpeta local y la base sigan coincidiendo")
    ap.add_argument("--limite", type=int, default=20,
                    help="cuántos links imprimir con --links (por defecto 20)")
    ap.add_argument("--parquet", nargs="?", const=True, metavar="RUTA",
                    help="solo exporta los parquet (audio + sensores) y sale. "
                         "RUTA opcional para el de audio; el de sensores usa "
                         "PARQUET_SENSORES o export/sensores.parquet")
    ap.add_argument("--sin-parquet", action="store_true",
                    help="en una ingesta real, no exportar el parquet al final")
    args = ap.parse_args()

    cfg = config()
    raiz = Path(args.carpeta).expanduser() if args.carpeta else cfg["raiz"]

    if args.links:
        with psycopg.connect(cfg["dsn"]) as conn:
            mostrar_links(conn, args.limite)
        return 0

    if args.parquet:
        ruta_audio = Path(args.parquet) if isinstance(args.parquet, str) else cfg["parquet_ruta"]
        with psycopg.connect(cfg["dsn"]) as conn:
            asegurar_columnas_url(conn)
            escritos = exportar_parquet(conn, ruta_audio, cfg["parquet_sensores"])
        for ruta, n in escritos:
            print(f"Parquet escrito: {ruta}  ({n} fila(s))")
        return 0

    if args.verificar:
        with psycopg.connect(cfg["dsn"]) as conn:
            return verificar(conn, cfg, raiz)

    todos = [p for p in raiz.rglob("*") if p.is_file()]
    audios   = sorted(p for p in todos if p.suffix.lower() in EXT_AUDIO)
    sensores = sorted(p for p in todos if p.suffix.lower() in EXT_SENSOR)

    if args.solo == "audio":
        sensores = []
    elif args.solo == "sensores":
        audios = []

    print(f"Carpeta : {raiz}")
    print(f"Origen  : {cfg['origen']}")
    print(f"Audios  : {len(audios)}")
    print(f"Sensores: {len(sensores)} archivo(s) .txt")

    # Archivos sueltos en la raíz: no se puede saber de qué estación son, así
    # que se sacan de la lista antes de empezar en vez de fallar uno por uno.
    sueltos = [p for p in audios + sensores if p.parent == raiz]
    if sueltos:
        print(f"\n  Aviso: {len(sueltos)} archivo(s) sueltos en la raíz, fuera de "
              f"cualquier carpeta de estación. Se omiten:")
        for p in sueltos[:5]:
            print(f"    - {p.name}")
        audios   = [p for p in audios   if p.parent != raiz]
        sensores = [p for p in sensores if p.parent != raiz]

    if not audios and not sensores:
        print("\nNada que hacer.")
        return 0

    # ---------------------- DRY RUN ------------------------------------------
    if args.dry_run:
        print("\n--- DRY RUN: no se escribe nada ---")
        if audios:
            print("\nAUDIOS")
            for p in audios[:10]:
                try:
                    m = metadatos_ffprobe(p)
                    f = fecha_de_texto(p.name)
                    print(f"  {p.relative_to(raiz).as_posix()}")
                    print(f"      estación={nombre_estacion(p, raiz)}  "
                          f"fecha={f.strftime('%Y-%m-%d %H:%M:%S') if f else 'NO DETECTADA'}")
                    print(f"      {m['duracion_seg']}s  {p.stat().st_size / 1048576:.1f} MB  "
                          f"{m['formato']}/{m['codec']}  {m['sample_rate']} Hz  "
                          f"{m['canales']} canal(es)")
                except Exception as e:
                    print(f"  {p.name}  -> ERROR: {e}")
            if len(audios) > 10:
                print(f"  ... y {len(audios) - 10} más")

        if sensores:
            print("\nSENSORES")
            for p in sensores[:10]:
                lec, aviso = parsear_txt(p)
                if not lec:
                    print(f"  {p.relative_to(raiz).as_posix()}  ->  se ignora ({aviso})")
                    continue
                print(f"  {p.relative_to(raiz).as_posix()}  -> {len(lec)} lectura(s)"
                      + (f"  [{aviso}]" if aviso else ""))
                for l in lec[:3]:
                    print(f"      {l['medido_en'].strftime('%Y-%m-%d %H:%M:%S')}  "
                          f"{l['humedad_pct']}% HR  {l['temperatura_c']} °C")
                if len(lec) > 3:
                    print(f"      ... y {len(lec) - 3} más")
            if len(sensores) > 10:
                print(f"  ... y {len(sensores) - 10} más")
        return 0

    # ---------------------- INGESTA REAL -------------------------------------
    with psycopg.connect(cfg["dsn"]) as conn:
        asegurar_columnas_url(conn)
        estaciones = cargar_estaciones(conn)
        almacen = cargar_almacenamiento(conn)
        if not estaciones:
            sys.exit("ERROR: la tabla `estacion` está vacía. ¿Corrió 02_semillas.sql?")
        if cfg["origen"] not in almacen:
            print(f"Aviso: `almacenamiento` no tiene la fila del origen "
                  f"'{cfg['origen']}'. Los archivos quedarán con url_onedrive "
                  f"= NULL. ¿Corrió 02_semillas.sql?", file=sys.stderr)
        print(f"\nEstaciones en la base: {', '.join(estaciones)}")

        a_n = a_o = a_f = s_n = s_o = s_f = s_i = 0

        if audios:
            print("\n=== AUDIOS ===")
            a_n, a_o, a_f = ingerir_audios(conn, cfg, raiz, audios, estaciones, almacen)

        if sensores:
            print("\n=== SENSORES ===")
            s_n, s_o, s_f, s_i = ingerir_sensores(conn, cfg, raiz, sensores, estaciones, almacen)

    print("\nResumen")
    print(f"  audios  : {a_n} nuevo(s), {a_o} ya estaban, {a_f} con error")
    print(f"  sensores: {s_n} lectura(s) nueva(s), {s_o} ya estaban, "
          f"{s_i} archivo(s) ignorado(s), {s_f} con error")

    if not args.sin_parquet:
        with psycopg.connect(cfg["dsn"]) as conn:
            escritos = exportar_parquet(conn, cfg["parquet_ruta"], cfg["parquet_sensores"])
        for ruta, n in escritos:
            print(f"\nParquet actualizado: {ruta}  ({n} fila(s))")

    print("\nPara ver los links:  python ingesta.py --links")
    return 1 if (a_f or s_f) else 0


if __name__ == "__main__":
    raise SystemExit(main())
