"""
Fase 3: pseudo-labeling de un lote.

El lote que debe procesarse lo inyecta temporalmente el orquestador
en la constante LOTE_ACTUAL antes de hacer `kaggle kernels push`.

La asignación de qué audios caen en qué lote es DETERMINÍSTICA
(índice % TOTAL_LOTES), así no hace falta ningún estado persistido
entre corridas del kernel: cada lote siempre procesa el mismo
subconjunto de archivos.
"""

import io
import json
import subprocess
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio


# =========================================================
# CONFIGURACIÓN
# =========================================================

SAMPLE_RATE = 44000
WINDOW_SECONDS = 5
WINDOW_LENGTH = SAMPLE_RATE * WINDOW_SECONDS

CLASSES = ["rain", "car"]
OTHER_THRESHOLD = 0.65

TOTAL_LOTES = 8
# Debe coincidir con TOTAL_LOTES en orchestrator_step.py.

PORCENTAJE_USAR = 0.70
# 70% del dataset para esta etapa de fine-tuning.


# =========================================================
# LOTE ACTUAL
# =========================================================
#
# IMPORTANTE:
# El orquestador reemplaza esta línea antes de subir el
# kernel a Kaggle.
#
# NO cambiar el comentario "# ORQUESTADOR_LOTE".
#
# Ejemplo:
#   LOTE_ACTUAL = 1
#   LOTE_ACTUAL = 2
#   ...
#   LOTE_ACTUAL = 8
#
LOTE_ACTUAL = 1  # ORQUESTADOR_LOTE

lote_actual = LOTE_ACTUAL


if not 1 <= lote_actual <= TOTAL_LOTES:
    raise ValueError(
        f"LOTE_ACTUAL inválido: {lote_actual}. "
        f"Debe estar entre 1 y {TOTAL_LOTES}."
    )


# =========================================================
# RUTAS KAGGLE
# =========================================================

IN_DIR = Path("/kaggle/input/datos_iniciales_raw")

OUT_DIR = Path("/kaggle/working/recortes")
OUT_DIR.mkdir(exist_ok=True, parents=True)


# =========================================================
# MODELO
# =========================================================
#
# Kaggle Models puede montar el modelo en distintos paths.
# Buscamos cualquier .pth dentro de /kaggle/input.
#

MODEL_PATH = next(Path("/kaggle/input").rglob("*.pth"), None)


class CNN1D(nn.Module):
    def __init__(self, channels):
        super().__init__()

        layers = []
        in_ch = 1

        for ch in channels:
            layers.append(
                nn.Conv1d(
                    in_ch,
                    ch,
                    kernel_size=5,
                    stride=2,
                    padding=2,
                )
            )
            layers.append(nn.BatchNorm1d(ch))
            layers.append(nn.ReLU())
            layers.append(nn.MaxPool1d(2))

            in_ch = ch

        self.features = nn.Sequential(*layers)
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.classifier = nn.Linear(
            channels[-1],
            len(CLASSES),
        )

    def forward(self, x):
        x = x.unsqueeze(1)
        x = self.features(x)
        x = self.pool(x)
        x = x.squeeze(-1)

        return self.classifier(x)


# =========================================================
# DECODIFICAR AUDIO
# =========================================================

def decode_full_audio(path: str) -> torch.Tensor:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        path,
        "-f",
        "wav",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "pipe:1",
    ]

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.decode(errors="ignore")
        )

    with io.BytesIO(proc.stdout) as buf:
        wav, sr = torchaudio.load(buf)

    # Convertir siempre a mono.
    return wav.mean(dim=0)


# =========================================================
# CARGAR MODELO
# =========================================================

model = CNN1D([16, 32, 64, 128])

if MODEL_PATH:
    print(f"Modelo encontrado: {MODEL_PATH}")

    model.load_state_dict(
        torch.load(
            str(MODEL_PATH),
            map_location="cpu",
        )
    )
else:
    print(
        "ADVERTENCIA: no se encontró ningún .pth "
        "en /kaggle/input. Se utilizará el modelo sin pesos."
    )

model.eval()


# =========================================================
# ENCONTRAR AUDIOS
# =========================================================

todos = sorted(
    IN_DIR.glob("*.wav")
)

if not todos:
    raise RuntimeError(
        f"No se encontraron archivos .wav en {IN_DIR}"
    )


# =========================================================
# SELECCIONAR 70%
# =========================================================

cantidad_usar = int(
    len(todos) * PORCENTAJE_USAR
)

usar = todos[:cantidad_usar]


# =========================================================
# SELECCIONAR LOTE
# =========================================================
#
# La distribución es determinística:
#
# lote 1 -> índices 0, 8, 16, ...
# lote 2 -> índices 1, 9, 17, ...
# ...
# lote 8 -> índices 7, 15, 23, ...
#

mi_lote = [
    f
    for i, f in enumerate(usar)
    if i % TOTAL_LOTES == (lote_actual - 1)
]


print(
    f"Lote {lote_actual}/{TOTAL_LOTES}: "
    f"{len(mi_lote)} audios de "
    f"{len(usar)} totales (70%)"
)


# =========================================================
# PROCESAR AUDIOS
# =========================================================

manifest = []

for audio_path in mi_lote:

    print(f"Procesando: {audio_path.name}")

    wav = decode_full_audio(
        str(audio_path)
    )

    n_windows = (
        wav.shape[0] // WINDOW_LENGTH
    )

    for i in range(n_windows):

        clip = wav[
            i * WINDOW_LENGTH:
            (i + 1) * WINDOW_LENGTH
        ]

        with torch.no_grad():

            probs = F.softmax(
                model(
                    clip.unsqueeze(0)
                ),
                dim=1,
            )[0]

        conf, idx = torch.max(
            probs,
            dim=0,
        )

        confidence = conf.item()

        if confidence >= OTHER_THRESHOLD:
            label = CLASSES[idx.item()]
        else:
            label = "other"

        out_name = (
            f"{audio_path.stem}_"
            f"{i * WINDOW_SECONDS}s_"
            f"{label}.wav"
        )

        torchaudio.save(
            str(OUT_DIR / out_name),
            clip.unsqueeze(0),
            SAMPLE_RATE,
        )

        manifest.append(
            {
                "file": out_name,
                "label_predicho": label,
                "confidence": confidence,
            }
        )


# =========================================================
# GUARDAR MANIFEST
# =========================================================

manifest_path = OUT_DIR / "manifest.json"

manifest_path.write_text(
    json.dumps(
        manifest,
        indent=2,
    )
)


print(
    f"Lote {lote_actual} listo: "
    f"{len(manifest)} recortes"
)

print(
    f"Manifest generado: {manifest_path}"
)
