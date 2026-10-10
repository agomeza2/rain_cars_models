# Clasificación con AST + Silero VAD

`clasificar.py` toma audios de las estaciones y genera un CSV con **una fila
por cada 10 s** y **un puntaje de 0 a 1 por clase**, más la fecha y hora real
y la humedad y temperatura del sensor en ese momento.

No necesita entrenamiento: usa dos modelos preentrenados.

- **AST** (`MIT/ast-finetuned-audioset-10-10-0.4593`), entrenado con AudioSet
  (527 tipos de sonido). Se agrupan en las clases del proyecto.
- **Silero VAD**, detector de voz liviano, como segunda opinión para `personas`.

## Instalación

Necesita **Python 3.10 o superior** y **ffmpeg** en el PATH.

**Windows**

```
winget install Gyan.FFmpeg
pip install -r requirements.txt
```

**Linux.** Instalar ffmpeg según la distro:

| Distro | Comando |
|---|---|
| Ubuntu / Debian / Mint | `sudo apt install ffmpeg python3-venv` |
| Fedora | `sudo dnf install ffmpeg-free` (alcanza para WAV) |
| Arch / Manjaro | `sudo pacman -S ffmpeg` |
| NixOS | `nix-shell -p ffmpeg python3` |

Usar un entorno virtual. Las distros recientes no dejan instalar con `pip` sobre
el Python del sistema (error `externally-managed-environment`):

```
python3 -m venv .venv
source .venv/bin/activate
# Sin GPU NVIDIA: torch en versión CPU (evita bajar ~2-3 GB de CUDA)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Con GPU NVIDIA basta `pip install -r requirements.txt`; el script usa la GPU
sola si está disponible (lo indica al arrancar: `AST en cuda`).

En Linux la ruta de los audios es la de donde esté montado el disco, por
ejemplo `/media/<usuario>/TOSHIBA EXT/AudiosSemilleroCABALAS`.

## Uso

```
python clasificar.py "F:/AudiosSemilleroCABALAS/Estación 1/01_08_2026_05_35_48.wav" -o resultado.csv
python clasificar.py "F:/AudiosSemilleroCABALAS/Estación bosque" -o bosque.csv
python clasificar.py audio.wav --excel        # separador ; y coma decimal
python clasificar.py audio.wav --sin-filtro   # sin el pasa-altos de 150 Hz
```

Se le pueden pasar archivos o carpetas (busca los `.wav` dentro). En CPU tarda
~1.5 min por audio de 15 min. La primera vez descarga AST (~350 MB) y Silero (~2 MB).

## Columnas del CSV

| Columna | Qué es |
|---|---|
| `audio`, `estacion` | Nombre del archivo y estación (carpeta `Estación ...`) |
| `fecha`, `hora` | Inicio real de la ventana: hora del nombre del archivo + desplazamiento |
| `desde`, `hasta` | Posición dentro del audio (mm:ss) |
| `aves`, `personas`, `vehiculos`, `perros`, `claxon`, `lluvia`, `viento` | Puntaje 0–1 de AST. **No suman 1**: un tramo puede tener varias clases a la vez |
| `voz_silero` | Fracción de la ventana que Silero marca como voz (0–1) |
| `humedad_pct`, `temperatura_c` | Lectura del `.txt` del sensor más cercana (vacío si está a más de 90 s) |

Como referencia, a partir de **0.20** el puntaje suele indicar que el sonido está presente.

## Qué tan confiable es (verificado a oído, oct 2026)

| Clase | Resultado |
|---|---|
| Personas | Muy bien. AST y Silero coinciden; Silero no da falsas alarmas con viento ni aves |
| Aves, perros | Muy bien |
| Vehículos | Bien cuando se oyen claro; a veces confunde **viento con vehículo** |
| Viento | Bien con el pasa-altos (incluye la etiqueta "Environmental noise" de AudioSet) |
| Claxon | **Mal**: no detectó ninguno de los dos verificados |
| Lluvia | **Sin evaluar**: ningún audio revisado tenía lluvia real |

Decisiones de preprocesado:

- **Pasa-altos de 150 Hz**: quita el retumbo del viento.
- **No se sube el volumen**: en el bosque convierte el siseo del micrófono en lluvia falsa.
- **ffmpeg en vez de `torchaudio.load`**, para que funcione igual en Windows y Linux.
