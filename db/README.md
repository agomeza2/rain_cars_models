# Empezar aquí — base de datos del semillero CABALAS

Guía para dejar todo funcionando en tu computador. Sigue los pasos en orden; cada
uno tiene una comprobación para saber si quedó bien antes de seguir.

Si quieres entender **por qué** está armado así (útil para la sustentación), eso
está en `DOCKER.md`. Este archivo es solo el cómo.

---

## Qué es esto

Una base de datos con la **información** de los audios: cuánto dura cada uno,
cuánto pesa, de qué estación y qué día es, la temperatura y humedad que había, y
el link para escucharlo.

Los archivos `.wav` **no** se guardan aquí — siguen en OneDrive. Esta base guarda
los datos sobre ellos, que es lo que permite buscar y filtrar sin descargar nada.

---

## Antes de empezar: instalar

Cuatro cosas, una sola vez:

| Programa | Cómo | Para qué |
|---|---|---|
| **Docker Desktop** | [docker.com](https://www.docker.com/products/docker-desktop/) | corre la base de datos |
| **Python 3.11+** | [python.org](https://www.python.org/downloads/) — marca *"Add Python to PATH"* | corre el script de carga |
| **ffmpeg** | `winget install Gyan.FFmpeg` | lee la duración de los audios |
| **Git** | [git-scm.com](https://git-scm.com/) | baja el proyecto |

Cierra y vuelve a abrir la terminal después de instalar. Comprueba:

```bat
docker --version
python --version
ffprobe -version
```

Si alguno dice "no se reconoce como un comando", no quedó en el PATH: reinstálalo
marcando la opción de agregarlo, o reinicia el computador.

---

## Paso 1 — Bajar el proyecto

```bat
git clone <url-del-repositorio>
cd Proyecto-git\db
```

Todo lo demás se hace desde esta carpeta `db`.

---

## Paso 2 — Tener los audios en tu disco

**Esto es lo que más se atasca, léelo con calma.**

El script lee archivos **locales**. Abrir la carpeta en el navegador no sirve:
tiene que verse en el Explorador de Windows como una carpeta normal.

1. Abre la app de **OneDrive** (ícono de nube, abajo a la derecha).
2. Engranaje → **Configuración** → pestaña **Cuenta** → **Agregar una cuenta**.
3. Inicia sesión con tu correo **de la universidad**.
   > Tu OneDrive personal y el institucional son cuentas distintas y se
   > sincronizan por separado. Si solo ves una carpeta llamada `OneDrive` a
   > secas, esa es la personal y los audios no están ahí.
4. Abre en el navegador la carpeta compartida del semillero y dale
   **Sincronizar** (o *Agregar acceso directo a Mis archivos*).
5. Aparece una carpeta nueva:
   `C:\Users\<tú>\OneDrive - <universidad>\...\AudiosSemilleroCABALAS`

**Comprobación:** ábrela en el Explorador. Debes ver adentro las carpetas de las
estaciones (`Estación 1`, etc.), y dentro de cada una los `.wav` y los `.txt`.

> Los audios pesan mucho (~113 MB cada uno). Si OneDrive los deja "solo en la
> nube", la primera carga los descarga y tarda. Para evitar sorpresas: clic
> derecho en la carpeta → **Conservar siempre en este dispositivo**.

---

## Paso 3 — Configurar tu `.env`

```bat
copy .env.example .env
notepad .env
```

Cambia **dos** líneas:

```
POSTGRES_PASSWORD=semillero2026
CARPETA_PROYECTO=C:\Users\TuUsuario\OneDrive - Universidad\...\AudiosSemilleroCABALAS
```

Tres reglas que evitan el 90% de los problemas:

- **La contraseña, solo letras y números.** Nada de `$`, `#`, espacios ni
  comillas: Docker y Python los interpretan distinto y luego la base te rechaza
  la clave.
- **Elígela ahora y no la cambies más.** La contraseña se graba dentro de la base
  la primera vez que se crea. Cambiarla después en el `.env` no cambia la base:
  solo hace que ya no coincidan.
- **`CARPETA_PROYECTO` apunta a la carpeta que *contiene* las estaciones**, no a
  una estación. Sin comillas y sin barra al final.

`.env` es tuyo y no se sube a git. Cada quien tiene el suyo.

---

## Paso 4 — Levantar la base

Con Docker Desktop **abierto** (espera a que el ícono de la ballena deje de
moverse):

```bat
docker compose up -d --build
```

La primera vez tarda varios minutos: descarga PostgreSQL. Después:

```bat
docker compose ps
```

**Comprobación:** `audios-db` debe decir **healthy**. Si dice `starting`, espera
20 segundos y repite. Si dice `exited`, mira `docker compose logs db`.

---

## Paso 5 — Comprobar que la base quedó bien

```bat
docker compose exec db psql -U audios_admin -d audios -c "\dt"
```

**Comprobación:** deben salir 7 tablas — `almacenamiento`, `anotacion`, `audio`,
`estacion`, `etiqueta`, `fuente`, `lectura_sensor`.

Si dice *"Did not find any relations"*, la base se creó vacía. Arréglalo así:

```bat
docker compose down -v
docker compose up -d --build
```

> `-v` borra los datos. Es seguro ahora que la base está vacía; **no** lo corras
> cuando ya tengas anotaciones hechas.

---

## Paso 6 — Cargar los datos

```bat
pip install -r requirements.txt
python ingesta.py --dry-run
```

`--dry-run` no escribe nada: solo muestra lo que encontró. **Comprobación:** debe
listar tus audios con su duración y tus sensores con humedad y temperatura.

Si todo se ve bien, carga primero los sensores (es rápido):

```bat
python ingesta.py --solo sensores
```

Y después los audios. Esto lee todos los archivos para calcular su huella, así
que con 16 GB tarda varios minutos. Imprime una línea por archivo, así que vas
viendo el avance:

```bat
python ingesta.py --solo audio
```

Puedes correrlo las veces que quieras: lo que ya está registrado se omite.

---

## Paso 7 — Usarlo

**Ver los links de los audios:**

```bat
python ingesta.py --links
```

**Comprobar que tu copia local y la base siguen coincidiendo:**

```bat
python ingesta.py --verificar
```

**Consultar desde la terminal:**

```bat
docker compose exec db psql -U audios_admin -d audios
```

Adentro, `\x` para que la salida se lea vertical, y `\q` para salir. Hay
consultas listas para copiar en `ejemplos/consultas_ejemplo.sql`.

**Consultar sin terminal:** abre <http://localhost:8080> (Adminer) y entra con:

- Motor: **PostgreSQL**
- Servidor: **db** ← el nombre del servicio, no `localhost`
- Usuario, contraseña y base: los de tu `.env`

---

## Si algo falla

| Lo que ves | Qué pasa |
|---|---|
| `no se reconoce como un comando` | Falta el PATH. Reinstala marcando "Add to PATH" y reabre la terminal. |
| `cannot connect to the Docker daemon` | Docker Desktop está cerrado. Ábrelo y espera. |
| `port is already allocated` | Algo usa el 5433. Cámbialo a `5434:5432` en `docker-compose.yml` y pon `PGPORT=5434` en el `.env`. |
| `password authentication failed` | El `.env` y la base no coinciden. Ver abajo. |
| `esta carpeta no existe en este equipo` | `CARPETA_PROYECTO` mal puesta. El error te lista las carpetas que sí encuentra. |
| `la estación 'X' no está en la tabla estacion` | El nombre de la carpeta no coincide con la tabla. Ver abajo. |
| `Did not find any relations` | La base se creó vacía. `docker compose down -v` y vuelve al paso 4. |
| `python ingesta.py` parece colgado | No lo está: está leyendo gigabytes. Déjalo. |

**Arreglar la contraseña** sin borrar nada:

```bat
docker compose exec db psql -U audios_admin -d audios -c "ALTER USER audios_admin PASSWORD 'semillero2026';"
```

Y pon exactamente esa misma en el `.env`. Funciona sin pedirte clave porque entra
desde dentro del contenedor.

**Agregar una estación nueva.** Si aparece una carpeta `Estación 2`, la base
tiene que saber que existe:

```bat
docker compose exec db psql -U audios_admin -d audios -c "INSERT INTO estacion (codigo, nombre) VALUES ('EST2','Estación 2');"
```

El `nombre` debe ser **idéntico** al de la carpeta: tildes, espacios y mayúsculas.
Agrega también esa línea a `sql/02_semillas.sql` y súbela a git, para que a los
demás les quede creada de una.

---

## Reglas del equipo

- **Nunca subas tu `.env` a git.** Tiene tu contraseña. Ya está en `.gitignore`;
  no lo saques de ahí.
- **No cambies los nombres de las carpetas de estación** en OneDrive sin avisar.
  La base las identifica por nombre exacto.
- **No corras `docker compose down -v` cuando ya haya anotaciones cargadas.**
  Borra los datos. Para apagar sin perder nada: `docker compose stop`.
- **Antes de tocar algo pesado, respalda:**
  ```bat
  docker compose exec db pg_dump -U audios_admin audios > respaldo.sql
  ```

---

## Comandos de uso diario

```bat
docker compose up -d                :: encender
docker compose stop                 :: apagar (los datos quedan)
docker compose ps                   :: ver estado
docker compose logs -f db           :: ver qué está pasando (Ctrl+C para salir)

python ingesta.py                   :: cargar lo nuevo
python ingesta.py --verificar       :: revisar que todo cuadre
python ingesta.py --links           :: ver los links
```
