"""
Fase 5: clasificación final del 100% del dataset con el CNN-DEEP, un CSV
por audio con una fila cada 5s (chunk == ventana, sin promediar sub-ventanas).
"""

import csv
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio

SAMPLE_RATE = 44000
WINDOW_SECONDS = 5
WINDOW_LENGTH = SAMPLE_RATE * WINDOW_SECONDS
CLASSES = ["rain", "car"]
OTHER_THRESHOLD = 0.65
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


class CNN1D(nn.Module):
    def __init__(self, channels):
        super().__init__()
        layers = []
        in_ch = 1
        for ch in channels:
            layers.append(nn.Conv1d(in_ch, ch, kernel_size=5, stride=2, padding=2))
            layers.append(nn.BatchNorm1d(ch))
            layers.append(nn.ReLU())
            layers.append(nn.MaxPool1d(2))
            in_ch = ch
        self.features = nn.Sequential(*layers)
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.classifier = nn.Linear(channels[-1], len(CLASSES))

    def forward(self, x):
        x = x.unsqueeze(1)
        x = self.features(x)
        x = self.pool(x)
        x = x.squeeze(-1)
        return self.classifier(x)


MODEL_PATH = next(Path("/kaggle/input").rglob("*.pth"), None)

model = CNN1D([16, 32, 64, 128])
model.load_state_dict(torch.load(str(MODEL_PATH), map_location=DEVICE))
model.to(DEVICE).eval()

IN_DIR = Path("/kaggle/input/datos_iniciales_raw")
OUT_DIR = Path("/kaggle/working/csvs_finales")
OUT_DIR.mkdir(exist_ok=True, parents=True)

archivos = sorted(IN_DIR.glob("*.wav"))
print(f"Clasificando {len(archivos)} audios")

for n, audio_path in enumerate(archivos, 1):
    wav, sr = torchaudio.load(str(audio_path))
    wav = wav.mean(dim=0)
    if sr != SAMPLE_RATE:
        wav = torchaudio.transforms.Resample(sr, SAMPLE_RATE)(wav)
    n_windows = wav.shape[0] // WINDOW_LENGTH
    rows = []
    for i in range(n_windows):
        clip = wav[i * WINDOW_LENGTH:(i + 1) * WINDOW_LENGTH].unsqueeze(0).to(DEVICE)
        with torch.no_grad():
            probs = F.softmax(model(clip), dim=1)[0].cpu()
        conf, idx = torch.max(probs, dim=0)
        label = CLASSES[idx.item()] if conf.item() >= OTHER_THRESHOLD else "other"
        rows.append({
            "chunk_index": i,
            "start_sec": round(i * WINDOW_SECONDS, 2),
            "end_sec": round((i + 1) * WINDOW_SECONDS, 2),
            "prediction": label,
            "confidence": round(conf.item() * 100, 2),
            "rain_prob": round(probs[0].item() * 100, 2),
            "car_prob": round(probs[1].item() * 100, 2),
        })
    if not rows:
        continue
    out_csv = OUT_DIR / f"{audio_path.stem}.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    if n % 20 == 0:
        print(f"progreso {n}/{len(archivos)}")

print("Terminado.")