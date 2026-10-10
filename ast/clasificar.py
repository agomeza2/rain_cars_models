"""
Clasifica audios de las estaciones con AST (AudioSet) + Silero VAD y escribe
un CSV con una fila por ventana de 10 s.

Cada clase tiene su propia columna con un puntaje de 0 a 1. Los puntajes son
independientes (no suman 1): un tramo puede tener aves 0.50 y personas 0.70 a
la vez. El puntaje de una clase es el máximo entre las etiquetas de AudioSet
que la componen (ver CLASES).

Columnas:
    audio, estacion, fecha, hora, desde, hasta,
    aves, personas, vehiculos, perros, claxon, lluvia, viento,
    voz_silero, humedad_pct, temperatura_c

    fecha/hora     inicio real de la ventana (hora de inicio del archivo,
                   tomada del nombre DD_MM_AAAA_HH_MM_SS, + desplazamiento)
    desde/hasta    posición dentro del audio (mm:ss)
    voz_silero     fracción de la ventana que Silero VAD marca como voz (0-1)
    humedad_pct,   lectura del .txt de sensor más cercana en el tiempo, de la
    temperatura_c  misma carpeta del audio; vacío si la más cercana está a más
                   de MAX_DESFASE_SENSOR_S

Decisiones tomadas tras verificar a oído dos rondas de prueba:
    - Pasa-altos de 150 Hz antes de AST: quita el retumbo del viento y ayuda a
      separar viento de vehículos. Se puede desactivar con --sin-filtro.
    - NO se normaliza el volumen: subir la ganancia en los audios del bosque
      convertía el siseo del micrófono en "lluvia" falsa.
    - "Environmental noise" cuenta como viento: en la Estación 1 fue la
      etiqueta que AST le puso al viento verificado.

El audio se decodifica con ffmpeg (no con torchaudio.load) para que funcione
igual en Windows y Linux.

Uso:
    python clasificar.py <audio.wav | carpeta> [...] -o resultados.csv
    python clasificar.py "F:/AudiosSemilleroCABALAS/Estación 1" -o est1.csv
    python clasificar.py audio.wav --excel        # ; y coma decimal

Requiere ffmpeg en el PATH. La primera vez descarga AST (~350 MB, Hugging
Face) y Silero VAD (~2 MB, torch.hub). En CPU tarda ~1.5 min por audio de
15 min.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import torch
from transformers import ASTFeatureExtractor, ASTForAudioClassification

MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000
VENTANA_S = 10
VENTANA_MIN_S = 2          # la última ventana parcial se clasifica si dura al menos esto
LOTE = 8
PASA_ALTOS_HZ = 150
UMBRAL_SILERO = 0.5
MAX_DESFASE_SENSOR_S = 90
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"   # solo AST; Silero va en CPU

CLASES = {
    "aves": ["Bird", "Bird vocalization, bird call, bird song", "Chirp, tweet",
             "Squawk", "Owl", "Hoot", "Crow", "Caw", "Pigeon, dove", "Coo",
             "Bird flight, flapping wings", "Chicken, rooster",
             "Crowing, cock-a-doodle-doo", "Fowl"],
    "personas": ["Speech", "Male speech, man speaking", "Female speech, woman speaking",
                 "Child speech, kid speaking", "Conversation", "Narration, monologue",
                 "Babbling", "Shout", "Yell", "Whistling", "Laughter", "Singing",
                 "Walk, footsteps"],
    "vehiculos": ["Vehicle", "Car", "Motor vehicle (road)", "Car passing by", "Truck",
                  "Bus", "Motorcycle", "Traffic noise, roadway noise", "Engine",
                  "Engine starting", "Idling", "Accelerating, revving, vroom",
                  "Light engine (high frequency)", "Medium engine (mid frequency)",
                  "Heavy engine (low frequency)"],
    "perros": ["Dog", "Bark", "Yip", "Howl", "Bow-wow", "Growling",
               "Whimper (dog)", "Canidae, dogs, wolves"],
    "claxon": ["Vehicle horn, car horn, honking", "Toot", "Air horn, truck horn"],
    "lluvia": ["Rain", "Raindrop", "Rain on surface", "Thunderstorm"],
    "viento": ["Wind", "Wind noise (microphone)", "Rustling leaves", "Environmental noise"],
}

RE_FECHA = re.compile(r"(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})_(\d{2})")


# =========================================================
# Audio
# =========================================================

def decodificar(ruta: Path, filtro: str | None = None) -> np.ndarray:
    """Decodifica a 16 kHz mono float32 con ffmpeg por pipe (sin archivos temporales)."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(ruta)]
    if filtro:
        cmd += ["-af", filtro]
    cmd += ["-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="ignore").strip()[:300])
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def ventanas(n_muestras: int) -> list[tuple[int, int]]:
    """Límites (inicio, fin) en muestras de cada ventana de VENTANA_S."""
    paso = SR * VENTANA_S
    limites = []
    for ini in range(0, n_muestras, paso):
        fin = min(ini + paso, n_muestras)
        if fin - ini >= SR * VENTANA_MIN_S:
            limites.append((ini, fin))
    return limites


def listar_audios(entradas: list[str]) -> list[Path]:
    audios = []
    for e in entradas:
        p = Path(e)
        if p.is_dir():
            audios += sorted(f for f in p.rglob("*") if f.suffix.lower() == ".wav")
        elif p.is_file():
            audios.append(p)
        else:
            print(f"[aviso] no existe: {p}", file=sys.stderr)
    return audios


# =========================================================
# Fecha, estación y sensores
# =========================================================

def fecha_de_nombre(nombre: str) -> datetime | None:
    m = RE_FECHA.search(nombre)
    if not m:
        return None
    d, mes, a, h, mi, s = map(int, m.groups())
    try:
        return datetime(a, mes, d, h, mi, s)
    except ValueError:
        return None


def estacion_de(ruta: Path) -> str:
    """Primera carpeta de la ruta cuyo nombre empieza por 'Estación'/'Estacion'."""
    # NFC: en Linux/macOS la "ó" puede venir descompuesta (o + tilde) según
    # cómo se copiaron los archivos, y entonces no coincidiría la comparación.
    for parte in ruta.parts:
        parte = unicodedata.normalize("NFC", parte)
        if parte.lower().startswith(("estación", "estacion")):
            return parte
    return unicodedata.normalize("NFC", ruta.parent.name)


_cache_sensores: dict[Path, tuple[list[datetime], list[tuple[float, float]]]] = {}


def sensores_de(carpeta: Path) -> tuple[list[datetime], list[tuple[float, float]]]:
    """
    Lee todos los .txt de la carpeta. Formato por línea:
        DD_MM_AAAA_HH_MM_SS, humedad, temperatura
    Devuelve (tiempos ordenados, [(humedad, temperatura), ...]).
    """
    if carpeta in _cache_sensores:
        return _cache_sensores[carpeta]
    lecturas = {}
    for txt in carpeta.glob("*.txt"):
        for linea in txt.read_text(encoding="utf-8", errors="replace").splitlines():
            campos = [c.strip() for c in linea.split(",")]
            if len(campos) < 3:
                continue
            momento = fecha_de_nombre(campos[0])
            try:
                hum, temp = float(campos[1]), float(campos[2])
            except ValueError:
                continue
            if momento and 0 <= hum <= 100 and -50 < temp < 80:
                lecturas[momento] = (hum, temp)
    tiempos = sorted(lecturas)
    resultado = (tiempos, [lecturas[t] for t in tiempos])
    _cache_sensores[carpeta] = resultado
    return resultado


def sensor_cercano(tiempos, valores, momento: datetime) -> tuple[float, float] | None:
    if not tiempos:
        return None
    i = bisect.bisect_left(tiempos, momento)
    candidatos = [j for j in (i - 1, i) if 0 <= j < len(tiempos)]
    j = min(candidatos, key=lambda k: abs((tiempos[k] - momento).total_seconds()))
    if abs((tiempos[j] - momento).total_seconds()) > MAX_DESFASE_SENSOR_S:
        return None
    return valores[j]


# =========================================================
# Modelos
# =========================================================

def cargar_ast():
    fe = ASTFeatureExtractor.from_pretrained(MODEL_ID)
    model = ASTForAudioClassification.from_pretrained(MODEL_ID).eval().to(DEVICE)
    label2id = {v: int(k) for k, v in model.config.id2label.items()}
    indices = {}
    for clase, etiquetas in CLASES.items():
        faltan = [e for e in etiquetas if e not in label2id]
        if faltan:
            print(f"[aviso] etiquetas que no existen en AudioSet ({clase}): {faltan}", file=sys.stderr)
        indices[clase] = [label2id[e] for e in etiquetas if e in label2id]
    return fe, model, indices


def puntajes_ast(wav: np.ndarray, limites, fe, model, indices) -> list[dict]:
    clips = [wav[a:b] for a, b in limites]
    filas = []
    for i in range(0, len(clips), LOTE):
        feats = fe(clips[i:i + LOTE], sampling_rate=SR, return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            probs = torch.sigmoid(model(**feats).logits).cpu().numpy()
        for p in probs:
            filas.append({c: float(p[idx].max()) for c, idx in indices.items()})
    return filas


def cargar_silero():
    # Silero hace torch.set_num_threads(1) al importarse, lo que deja a AST en
    # un solo núcleo (~4x más lento). Se restaura el valor anterior.
    hilos = torch.get_num_threads()
    modelo, utils = torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True, verbose=False)
    torch.set_num_threads(hilos)
    return modelo, utils[0]


def voz_por_ventana(wav: np.ndarray, limites, silero, get_speech_timestamps) -> list[float]:
    segs = get_speech_timestamps(torch.from_numpy(wav), silero, sampling_rate=SR,
                                 threshold=UMBRAL_SILERO, min_speech_duration_ms=300)
    fracciones = []
    for a, b in limites:
        voz = sum(max(0, min(b, s["end"]) - max(a, s["start"])) for s in segs)
        fracciones.append(voz / (b - a))
    return fracciones


# =========================================================
# Main
# =========================================================

def mmss(muestras: int) -> str:
    s = int(round(muestras / SR))
    return f"{s // 60:02d}:{s % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description="AST + Silero VAD -> CSV con puntaje 0-1 por clase cada 10 s")
    ap.add_argument("entradas", nargs="+", help="archivos .wav o carpetas (se buscan .wav recursivamente)")
    ap.add_argument("-o", "--salida", default="resultados_ast.csv", help="CSV de salida")
    ap.add_argument("--sin-filtro", action="store_true", help=f"no aplicar el pasa-altos de {PASA_ALTOS_HZ} Hz")
    ap.add_argument("--excel", action="store_true", help="separador ';' y coma decimal (Excel en español)")
    args = ap.parse_args()

    audios = listar_audios(args.entradas)
    if not audios:
        print("No se encontraron audios .wav", file=sys.stderr)
        return 1
    print(f"{len(audios)} audio(s) -> {args.salida}  (AST en {DEVICE})")

    fe, model, indices = cargar_ast()
    silero, get_speech_timestamps = cargar_silero()
    filtro = None if args.sin_filtro else f"highpass=f={PASA_ALTOS_HZ}:poles=2"

    sep = ";" if args.excel else ","
    num = (lambda x: f"{x:.3f}".replace(".", ",")) if args.excel else (lambda x: f"{x:.3f}")
    num1 = (lambda x: f"{x:.1f}".replace(".", ",")) if args.excel else (lambda x: f"{x:.1f}")
    columnas = (["audio", "estacion", "fecha", "hora", "desde", "hasta"]
                + list(CLASES) + ["voz_silero", "humedad_pct", "temperatura_c"])

    with open(args.salida, "w", newline="", encoding="utf-8-sig" if args.excel else "utf-8") as f:
        w = csv.writer(f, delimiter=sep)
        w.writerow(columnas)

        for n, ruta in enumerate(audios, 1):
            t0 = time.time()
            try:
                crudo = decodificar(ruta)
                filtrado = decodificar(ruta, filtro) if filtro else crudo
            except RuntimeError as e:
                print(f"[{n}/{len(audios)}] ERROR {ruta.name}: {e}", file=sys.stderr)
                continue

            limites = ventanas(len(crudo))
            if not limites:
                print(f"[{n}/{len(audios)}] {ruta.name}: audio demasiado corto, se omite")
                continue

            ast = puntajes_ast(filtrado, limites, fe, model, indices)
            voz = voz_por_ventana(crudo, limites, silero, get_speech_timestamps)
            inicio = fecha_de_nombre(ruta.stem)
            tiempos, valores = sensores_de(ruta.parent)
            estacion = estacion_de(ruta)

            for (a, b), pts, v in zip(limites, ast, voz):
                momento = inicio + timedelta(seconds=a / SR) if inicio else None
                sensor = sensor_cercano(tiempos, valores, momento) if momento else None
                w.writerow(
                    [ruta.stem, estacion,
                     momento.strftime("%Y-%m-%d") if momento else "",
                     momento.strftime("%H:%M:%S") if momento else "",
                     mmss(a), mmss(b)]
                    + [num(pts[c]) for c in CLASES]
                    + [num(v),
                       num1(sensor[0]) if sensor else "",
                       num1(sensor[1]) if sensor else ""])
            f.flush()
            print(f"[{n}/{len(audios)}] {ruta.name}: {len(limites)} ventanas, {time.time() - t0:.0f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
