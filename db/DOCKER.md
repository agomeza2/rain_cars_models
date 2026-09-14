# Base de datos de audios y sensores — guía desde cero

Semillero CABALAS. Explica **qué hace cada pieza y por qué está ahí**, no solo
los comandos. Las secciones marcadas 🎓 son las que preguntan en sustentación.

---

## 1. Qué problema resuelve esto

Tenemos, en OneDrive institucional, grabaciones de campo (`.wav`) y las lecturas
de temperatura y humedad del sensor de cada micrófono (`.txt`). Y tenemos un
modelo que clasifica sonidos. Falta la pieza del medio: **un sitio donde consten
los datos de cada archivo** — cuánto dura, cuánto pesa, de qué estación y qué
día es, qué clases contiene, si va a train o a test, y **cómo llegar a él**.

Sin eso, el dataset vive en nombres de archivo y en carpetas. Eso se rompe
apenas entra la segunda persona a trabajar.

Esta base guarda **metadatos y links**, no audio.

---

## 2. Qué es Docker, en una frase

Docker empaqueta un programa junto con todo lo que necesita para correr
(sistema, librerías, configuración) en una **imagen**. De esa imagen se arrancan
**contenedores**, que son ejecuciones desechables de esa imagen.

| Palabra        | Qué es                            | Analogía            |
|----------------|-----------------------------------|---------------------|
| **Imagen**     | Plantilla de solo lectura         | El instalador       |
| **Contenedor** | Una ejecución de la imagen        | El programa abierto |
| **Volumen**    | Disco que sobrevive al contenedor | Tus documentos      |

🎓 **Por qué importa aquí:** tú tienes PostgreSQL instalado nativo en Windows. Un
compañero tendría que instalarlo también, con la misma versión, la misma
configuración y el mismo esquema, y acordarse de correr los scripts en el orden
correcto. Con Docker clona el repo, corre un comando y tiene una base
**idéntica** a la tuya. La frase para la sustentación: *reproducibilidad del
entorno*.

---

## 3. Los archivos de esta carpeta

```
db/
├── Dockerfile                    # define la imagen audios-db:1.0
├── .dockerignore                 # qué NO se manda al construir la imagen
├── docker-compose.yml            # levanta la base + Adminer juntos
├── .env.example                  # plantilla de variables (el .env real NO va a git)
├── DOCKER.md                     # este archivo
├── ingesta.py                    # cataloga los .wav y los .txt en la base
├── requirements.txt              # dependencias de ingesta.py
├── sql/                          # ← se ejecuta AUTOMÁTICAMENTE al crear la base
│   ├── 01_schema.sql             #   tablas, restricciones, vistas
│   └── 02_semillas.sql           #   almacenamiento, estaciones, clases
└── ejemplos/                     # ← NO se ejecuta nunca solo
    └── consultas_ejemplo.sql
```

### 🎓 Por qué el esquema va horneado en la imagen

La imagen oficial de Postgres tiene una regla: **todo archivo `.sql` que
encuentre en `/docker-entrypoint-initdb.d/` lo ejecuta la primera vez que
inicializa su directorio de datos.**

El `Dockerfile` hace exactamente una cosa útil:

```dockerfile
COPY sql/ /docker-entrypoint-initdb.d/
```

Así la estructura de la base deja de ser un paso manual que alguien puede
olvidar, y pasa a ser parte del artefacto versionado en git.

Dos detalles que valen puntos:

- **Solo corre la primera vez.** Si el volumen ya tiene datos, Postgres ignora
  esa carpeta; por eso un reinicio no te borra la base. La contracara: **cambiar
  `01_schema.sql` no cambia una base que ya existe** — hay que borrar el volumen
  (sección 10) o escribir una migración.
- **`ejemplos/` está fuera de `sql/` a propósito.** Si estuviera dentro, Postgres
  ejecutaría las consultas de práctica al crear la base.

---

## 4. Los dos PostgreSQL y el asunto de los puertos

| Cuál                        | Puerto | Quién lo usa                |
|-----------------------------|--------|-----------------------------|
| PostgreSQL nativo de Windows | 5432  | lo instalaste tú, aparte    |
| PostgreSQL del contenedor    | 5433  | **este proyecto**           |

En `docker-compose.yml`:

```yaml
ports:
  - "5433:5432"
```

Se lee **`puerto_en_tu_windows : puerto_dentro_del_contenedor`**. Adentro
Postgres siempre escucha en 5432 (es su puerto estándar); afuera lo publicamos en
5433 porque el 5432 ya está ocupado. Dos programas no pueden escuchar el mismo
puerto: con `"5432:5432"` el contenedor no arrancaría.

🎓 Esto es *port mapping*, una de las cosas que hace Docker: aislar la red del
contenedor y exponer solo lo que tú decidas.

**Conclusión práctica:** el proyecto siempre usa el **5433**. Si te sale "no
existe la tabla audio", casi seguro te conectaste al 5432, o sea al Postgres
nativo, que está vacío.

---

## 5. Arrancar

```bat
cd C:\Users\Home\Desktop\Proyecto-git\db
copy .env.example .env
```

Abre `.env`, cambia `POSTGRES_PASSWORD` y pon tu `CARPETA_PROYECTO`. Después:

```bat
docker compose up -d
```

`up` construye la imagen (la primera vez descarga Postgres, tarda) y arranca los
contenedores. `-d` es *detached*: quedan en segundo plano y te devuelve la
terminal.

```bat
docker compose ps
```

Quieres ver `audios-db` en estado **healthy**. Ese "healthy" viene del
`healthcheck` del compose, que corre `pg_isready` cada 5 segundos. Sin él Docker
solo sabría que el proceso está vivo, no que la base ya acepta consultas — y son
cosas distintas: Postgres tarda unos segundos en estar listo.

---

## 6. Entrar a la base

`psql` no te funciona en la terminal porque el instalador de PostgreSQL no agregó
su carpeta al **PATH** (la lista de sitios donde Windows busca ejecutables). No
hace falta arreglarlo: **el contenedor trae su propio `psql`**, y además es el
que corresponde a la versión de este proyecto.

```bat
docker compose exec db psql -U audios_admin -d audios
```

- `docker compose exec` → ejecuta algo **dentro de un contenedor que ya corre**
- `db` → el nombre del servicio en `docker-compose.yml`
- `psql -U audios_admin -d audios` → el cliente, con usuario y base

Como corre *dentro*, el mapeo de puertos no interviene: usa el 5432 interno.

### Comprobar que el esquema se creó

```
\dt                              -- 7 tablas
\dv                              -- 3 vistas
SELECT COUNT(*) FROM etiqueta;   -- 13
SELECT * FROM estacion;          -- Estación 1
SELECT * FROM almacenamiento;    -- la fila con las URL de SharePoint
\q
```

Si `\dt` dice "Did not find any relations", el esquema no se ejecutó → sección 10.

### La alternativa sin terminal: Adminer

Abre <http://localhost:8080>:

- Motor: **PostgreSQL**
- Servidor: **db** ← el nombre del servicio, no `localhost`
- Usuario / clave / base: los de tu `.env`

🎓 Por qué `db` y no `localhost`: Adminer corre en **otro contenedor**. Dentro de
la red que crea Docker Compose, cada servicio es alcanzable por su nombre; para
Adminer, `localhost` sería él mismo.

---

## 7. Pasárselo a los compañeros

Lo que se comparte es el **repositorio**, no una base exportada. Ellos hacen:

```bat
git clone <url-del-repo>
cd Proyecto-git\db
copy .env.example .env
```

Editan `.env` con **su** contraseña y **su** ruta de OneDrive, y:

```bat
docker compose up -d
```

Y ya tienen la misma base que tú, con las mismas tablas y las mismas clases.

🎓 **Por qué esto funciona y por qué `.env` no va a git:** el repo lleva la
*receta* (Dockerfile, compose, SQL), no los *secretos* ni las rutas de cada
máquina. `.env.example` documenta qué variables existen; `.env` las llena y se
queda en cada computador. Es lo que permite que el mismo repo funcione en cinco
equipos distintos sin editar código.

Asegúrate de que la raíz del repo tenga un `.gitignore` con al menos:

```
.env
__pycache__/
*.pyc
```

**Lo que NO se comparte por git:** los audios (están en OneDrive) y los datos de
la base (están en el volumen de Docker, en cada máquina). Si en algún momento
quieren compartir el *contenido* de la base:

```bat
docker compose exec db pg_dump -U audios_admin audios > respaldo.sql
```

y el otro lo restaura con:

```bat
docker compose exec -T db psql -U audios_admin -d audios < respaldo.sql
```

---

## 8. Meter los datos: `ingesta.py`

La base arranca vacía. `ingesta.py` la llena.

```bat
pip install -r requirements.txt
python ingesta.py --dry-run     :: muestra qué haría, sin escribir nada
python ingesta.py               :: de verdad
python ingesta.py --links       :: imprime los links de lo que ya está
```

### Antes de correrlo: sincronizar la carpeta

El script lee **archivos locales**, no páginas web. La carpeta de OneDrive tiene
que verse en el Explorador de Windows. Para lograrlo:

1. Abre la carpeta en el navegador (el link de SharePoint del proyecto).
2. Botón **Sincronizar** (o **Agregar acceso directo a Mis archivos**).
3. Se abre la app de OneDrive y la carpeta aparece en el Explorador, normalmente
   bajo `C:\Users\<tu usuario>\OneDrive - <nombre de la universidad>\`.
4. Copia esa ruta a `CARPETA_PROYECTO` en `.env` — apuntando a
   **AudiosSemilleroCABALAS**, no a "Estación 1".

También hace falta **ffprobe** en el PATH (`winget install Gyan.FFmpeg`,
comprobar con `ffprobe -version`). Es quien lee duración, formato, sample rate y
canales: la misma herramienta que ya usa `ffmpeg/minute.py` en el pipeline de
inferencia, para tener un solo criterio en todo el proyecto.

> ⚠️ OneDrive tiene "Archivos a petición": un archivo que aparece en el
> Explorador puede estar solo en la nube y ocupar 0 bytes en disco. Leerlo
> dispara la descarga, así que la primera ingesta puede tardar y consumir datos.
> Para evitar sorpresas: clic derecho en la carpeta → **Conservar siempre en este
> dispositivo**.

### Qué saca de cada archivo

De los **`.wav`**: nombre, **peso en bytes**, **duración en segundos**, formato,
códec, sample rate, canales, bitrate, sha256 y la fecha de grabación.

De los **`.txt`**: fecha y hora de la medición, **humedad (%)** y
**temperatura (°C)**.

### 🎓 La fecha sale del nombre, no del sistema de archivos

OneDrive reescribe la fecha de modificación al sincronizar, así que `st_mtime`
diría "hoy" para una grabación de agosto. El nombre `01_08_2026_00_06_05.wav` sí
es el dato real de campo, y se interpreta como **DD_MM_AAAA_HH_MM_SS** (día
primero, como se escribe en Colombia). Eso se confirma contra los CSV que ya
están en el repo: `01-08-2026.csv` y `2026-08-01-05-35-48.csv` son la misma fecha.

Se guarda con zona horaria de Bogotá. Sin zona, Postgres asumiría UTC y todas las
grabaciones quedarían corridas cinco horas — y el cruce con los sensores daría
mal.

### 🎓 Cómo lee los `.txt` sin romperse

No lee línea por línea. Busca cada aparición de una fecha en todo el texto y toma
los **dos primeros números que vengan después** de ella. Con eso, el mismo código
lee las tres formas que puede escribir un datalogger:

```
01_08_2026_00_06_05, 58.83, 18.51        (todo en una línea)
01_08_2026_00_06_05
58.83
18.51                                     (un valor por línea)
...;58,83;18,51 ...;61,20;17,94           (varias lecturas seguidas)
```

Tolera el separador (coma, punto y coma, tabulación, espacio, salto de línea) y
la coma decimal, porque el formato exacto puede cambiar entre versiones del
firmware y la ingesta no debería caerse por eso. Lo que **no** tolera es el
orden: humedad primero, temperatura después, como está documentado. Si el sensor
lo cambiara, se cambia en el código — a propósito y a la vista, no adivinando.

Un `.txt` sin ninguna fecha (notas de campo, por ejemplo) se ignora sin error: no
es un archivo de sensor.

### 🎓 Cómo agrupa los archivos

La **primera subcarpeta** bajo la raíz es la estación:

```
AudiosSemilleroCABALAS/Estación 1/01_08_2026_00_06_05.wav   ->  Estación 1
```

Ese nombre tiene que existir en la tabla `estacion`. Cuando instalen la Estación
2, se agrega una fila ahí y la ingesta la reconoce sola.

Los audios se agrupan además en **sesiones de grabación** por estación y día
(`EST1-2026-08-01`). Eso no es cosmético: la partición train/val/test se decide
por sesión, no por archivo (sección 9.4).

### 🎓 La carpeta local y la nube son dos copias

El script lee archivos **locales**; el link apunta a la **nube**. Son dos copias
del mismo archivo, y la fila de la base las une: los metadatos salen de la copia
local, el link apunta a la de SharePoint.

Si la carpeta está sincronizada por OneDrive, las dos copias se mantienen iguales
solas. Si no lo está — por ejemplo si se desvinculó la cuenta y quedó la carpeta
suelta — se van separando sin que nadie lo note, y el daño es silencioso: una
fila con metadatos de un archivo que ya no existe y un link que sí. Ninguna de
las dos mitades falla por su cuenta.

Por eso existe:

```bat
python ingesta.py --verificar
```

Responde tres cosas: si cada audio de la base sigue teniendo su archivo local, si
alguno cambió de tamaño desde que se registró, y si hay archivos locales sin
ingerir. Vale la pena correrlo cada vez que se mueva algo de sitio.

**Lo que sí tiene que coincidir** es la estructura por debajo de la raíz:
`ruta_relativa` se calcula relativa a `CARPETA_PROYECTO` y luego se pega al
prefijo de SharePoint. Es decir, `CARPETA_PROYECTO` local debe corresponder a
`.../Documents/Tesis-Semillero/AudiosSemilleroCABALAS` en la nube, y dentro de
las dos tiene que estar `Estación 1\`. Si eso cuadra, da igual dónde esté la
carpeta local o cómo se llame.

### 🎓 Por qué se puede correr mil veces

Es **idempotente**: correrlo dos veces no duplica nada.

- Los audios se reconocen por `sha256`: si el mismo contenido ya está, se omite —
  aunque el archivo tenga otro nombre.
- Las lecturas de sensor se reconocen por `(estación, instante)`: dos `.txt`
  distintos pueden traer la misma medición, y la base solo guarda una.
- Cada archivo va en su propia transacción, así que uno corrupto no deja la base
  a medias ni tumba el resto de la corrida.

Probado: con una carpeta que traía un audio duplicado bajo otro nombre y un
`.txt` de notas sin datos, la primera corrida insertó 3 audios y 6 lecturas,
omitió el duplicado, ignoró las notas; la segunda corrida no insertó nada.

---

## 9. 🎓 Las decisiones de diseño

Cada una tiene la misma forma: *se eligió A en vez de B, y B falla por esta razón
concreta*.

### 9.1 El audio no se guarda en Postgres, solo la referencia

Un WAV de 15 min a 44.1 kHz pesa unos 150 MB. Metido como `BYTEA`:

- el backup pasa de megabytes a cientos de gigabytes;
- un `SELECT *` descuidado arrastra el archivo entero por la red;
- para oírlo hay que sacarlo primero de la base.

Se guarda la ubicación. La base responde *"¿qué audios de la Estación 1 con
humedad mayor a 90% duran más de 10 s?"* en milisegundos, y el archivo se
descarga solo cuando de verdad se necesita.

### 9.2 El link no se guarda: se calcula

Esta es la que más se pregunta. En la tabla `audio` solo hay:

```
origen         = 'onedrive-cabalas'
ruta_relativa  = 'Estación 1/01_08_2026_00_06_05.wav'
```

La dirección de SharePoint vive en **una fila** de la tabla `almacenamiento`, y
la vista `v_audios` junta las dos cosas para producir el link completo.

Si guardáramos el link entero en cada fila, el día que cambie la cuenta, el
tenant o la carpeta habría que reescribir **todas** las filas. Así es un `UPDATE`
de una fila y todos los links quedan corregidos de golpe.

La ruta es relativa a la raíz del proyecto, no la ruta de Windows: en tu equipo
la carpeta sincronizada está en un sitio y en el de tu compañero en otro, y la
misma fila tiene que servir en los dos.

> Es el principio de separar *identidad* de *dirección*. La base guarda qué
> archivo es; dónde vive hoy es configuración.

### 9.3 Las clases son filas, no columnas

La alternativa mala sería `audio(es_lluvia BOOLEAN, es_carro BOOLEAN, ...)`. Con
eso, agregar "motosierra" es un `ALTER TABLE` sobre una tabla con datos, más
tocar todo el código que lee esas columnas.

Con `etiqueta` + `anotacion`, agregar una clase es:

```sql
INSERT INTO etiqueta (nombre, categoria, descripcion)
VALUES ('motosierra', 'antropofonia', 'Indicador de tala');
```

Y como `anotacion` es una tabla puente (N:M), un mismo clip puede tener **lluvia
y carro al tiempo**, que es literalmente lo que pasa en campo.

`anotacion` guarda además **quién lo dijo** (`origen_anotacion`: humano o modelo,
con `confianza` y `modelo`). Así se entrena solo con lo humano y lo del modelo
queda para revisión.

### 9.4 `particion` está en `fuente`, no en `audio`

De una sesión de grabación salen muchos clips. Si la partición estuviera en
`audio`, un `random_split` podría mandar el clip 12 a train y el 13 a test.
Comparten micrófono, noche y ruido de fondo: el modelo reconocería *esa sesión*,
no *esa clase*, y el accuracy de test saldría inflado. Eso es **data leakage**.

Con `particion` en `fuente`, la decisión se toma una vez por sesión y todos sus
clips la heredan. Un `CHECK` impide que alguien escriba `'entrenamiento'`.

### 9.5 Los sensores van en su propia tabla

No son columnas de `audio` porque:

- las cadencias no coinciden: el sensor mide cada tanto y el micrófono graba
  cuando graba; no hay un uno-a-uno;
- hay lecturas sin audio y audios sin lectura exacta;
- si mañana agregan un sensor de presión o de luz, es una columna en
  `lectura_sensor`, no una migración de la tabla de audios.

El cruce se hace **por tiempo**, en la vista `v_audio_ambiente`: para cada audio
busca la lectura más cercana de su misma estación, y reporta `desfase_seg` para
que puedas descartar los cruces que quedaron lejos.

🎓 **Y esto es lo que hace valiosa la combinación:** la humedad es evidencia
física independiente del audio. Si el modelo dice "lluvia" y el higrómetro
marcaba 95%, hay confirmación cruzada. Si marcaba 40%, esa predicción hay que
revisarla. Es control de calidad de etiquetas sin escuchar todo a mano — y solo
es posible porque los dos tipos de archivo están en la misma base.

---

## 10. Problemas comunes

**`docker compose up` dice que el puerto está ocupado**
Algo más usa el 5433. Cámbialo a `"5434:5432"` y actualiza `PGPORT` en `.env`.

**`\dt` no muestra tablas**
El esquema solo corre al crear el volumen. Si el volumen ya existía, hay que
rehacerlo:

```bat
docker compose down -v
docker compose up -d --build
```

> ⚠️ `-v` **borra el volumen y con él todos los datos**. Sin problema mientras la
> base esté vacía; con anotaciones reales dentro, esto no se hace: se escribe una
> migración.

**Cambié `01_schema.sql` y no pasó nada**
Lo mismo de arriba: los scripts de init solo corren una vez.

**`ingesta.py` dice que la carpeta no existe**
El propio error lista las carpetas de OneDrive que sí hay en el equipo y
propone candidatas. Lee esa lista antes de nada.

La causa más común: en el equipo solo está sincronizada la cuenta **personal**
de OneDrive (carpeta `OneDrive` a secas) y no la **institucional** (carpeta
`OneDrive - <universidad>`). Son dos cuentas distintas y la app las sincroniza
por separado:

1. Clic en el ícono de nube de OneDrive (barra de tareas) → engranaje →
   **Configuración** → pestaña **Cuenta** → **Agregar una cuenta**.
2. Inicia sesión con el correo de la universidad.
3. Aparece una carpeta nueva `C:\Users\<tú>\OneDrive - <universidad>`.
4. Abre la carpeta de los audios en OneDrive web, botón **Sincronizar**.
5. Copia la ruta que muestra el Explorador y ponla en `CARPETA_PROYECTO`.

**Los links apuntan a la cuenta equivocada**
Pasa si los archivos se movieron a otro OneDrive. No hay que reingerir nada:
es un `UPDATE` de la única fila de `almacenamiento` (ver `sql/02_semillas.sql`,
que trae el UPDATE listo como comentario).

**`la estación 'X' no está en la tabla estacion`**
El nombre de la subcarpeta no coincide con `estacion.nombre`. O corriges el
nombre de la carpeta, o agregas la estación:

```sql
INSERT INTO estacion (codigo, nombre) VALUES ('EST2', 'Estación 2');
```

**Un `.txt` sale como "se ignora"**
No tiene una fecha con formato `DD_MM_AAAA_HH_MM_SS`. Si sí es un archivo de
sensor, corre `python ingesta.py --dry-run` y mira qué se está leyendo.

**Ver qué pasó al arrancar la base**

```bat
docker compose logs db
```

Ahí aparecen los errores de SQL del arranque, que es donde se ve si el esquema
falló.

**Docker Desktop no responde**
En Windows corre sobre WSL2. Reinicia Docker Desktop; si sigue, `wsl --shutdown`
en PowerShell y vuelve a abrirlo.

---

## 11. Comandos del día a día

```bat
docker compose up -d              :: levantar
docker compose ps                 :: estado
docker compose logs -f db         :: logs en vivo (Ctrl+C para salir)
docker compose exec db psql -U audios_admin -d audios   :: entrar a la base
docker compose stop               :: apagar SIN borrar datos
docker compose down               :: apagar y borrar contenedores (el volumen queda)
docker compose down -v            :: ⚠️ además borra el volumen = borra los datos
docker compose up -d --build      :: reconstruir tras cambiar el Dockerfile

python ingesta.py --dry-run       :: ver qué haría
python ingesta.py                 :: ingerir
python ingesta.py --solo sensores :: solo los .txt
python ingesta.py --links         :: ver los links
python ingesta.py --verificar     :: ¿la carpeta local y la base siguen coincidiendo?
```

Respaldo antes de cualquier cosa arriesgada:

```bat
docker compose exec db pg_dump -U audios_admin audios > respaldo.sql
```

---

## 12. Qué falta

- [ ] `docker compose up -d` y confirmar el esquema (sección 6)
- [ ] Sincronizar `AudiosSemilleroCABALAS` en Windows (sección 8)
- [ ] `python ingesta.py --dry-run` y después la ingesta real
- [ ] Correr las consultas de `ejemplos/consultas_ejemplo.sql`
- [ ] Repartir train/val/test sobre las fuentes (consulta 7 del archivo de ejemplos)
- [ ] Etiquetar: llenar `anotacion`, a mano o volcando las predicciones de
      `ffmpeg/minute.py`
- [ ] Conectar el entrenamiento a `v_labels` en vez de leer carpetas del disco
