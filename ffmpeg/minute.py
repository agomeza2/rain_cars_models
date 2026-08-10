"""
RAM-only audio classification over 1-minute windows, using your existing
Wav2Vec2Classifier (see test.py).

Key design decisions vs. a naive port of test.py:

1. torchaudio.load() accepts a file-like object (anything with .read()),
   so we never need to write a .wav to disk -- we hand it an in-memory
   BytesIO directly. That's true RAM-only, no /dev/shm needed.

2. ffmpeg is called ONCE for the whole 15-minute file (decoded to 16kHz
   mono PCM), not once per chunk. The resulting waveform tensor lives in
   RAM and all 1-minute / 5-second windowing is just tensor slicing --
   cheap, no repeated subprocess calls.

3. Your models were trained on fixed 5-second clips (AUDIO_DURATION = 5).
   Feeding them a full 60s chunk would just get silently trimmed to the
   first 5s by your existing preprocess_audio(). Instead, each 1-minute
   window is split into 5s sub-windows, each one is classified, and the
   per-minute row in the CSV is the average of those sub-window
   probabilities (plus the majority-vote label). This keeps every
   inference call matching the distribution the model was trained on.

4. The ONLY disk write in the entire script is the final CSV.
"""

import csv
import io
import subprocess

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio

from transformers import Wav2Vec2Model

# =========================================================
# CONFIG (same as your test.py)
# =========================================================

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

SAMPLE_RATE = 16000
WINDOW_SECONDS = 5          # model's native input length
CHUNK_SECONDS = 60          # granularity you want in the CSV
WINDOW_LENGTH = SAMPLE_RATE * WINDOW_SECONDS

CLASSES = ["rain", "car"]

# If the model's top confidence for a window is below this, we don't
# trust either label and call it "other" instead of forcing a guess.
# Tune this by eye against a few known "other" clips (e.g. silence,
# speech, wind) -- start around 0.6-0.7 and adjust.
OTHER_THRESHOLD = 0.65

# =========================================================
# MODEL DEFINITION (unchanged from your test.py)
# =========================================================

class Wav2Vec2Classifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.wav2vec = Wav2Vec2Model.from_pretrained("facebook/wav2vec2-base")
        self.classifier = nn.Linear(768, len(CLASSES))

    def forward(self, x):
        outputs = self.wav2vec(x)
        hidden_states = outputs.last_hidden_state
        pooled = hidden_states.mean(dim=1)
        return self.classifier(pooled)


def load_wav2vec(model_path):
    model = Wav2Vec2Classifier()
    model.load_state_dict(torch.load(model_path, map_location=DEVICE))
    model.to(DEVICE)
    model.eval()
    return model


# =========================================================
# RAM-ONLY DECODE: one ffmpeg call for the whole file
# =========================================================

def decode_full_audio(input_path: str) -> torch.Tensor:
    """
    Decode the entire input file to 16kHz mono PCM via ffmpeg, piped
    straight to stdout (no temp file), then load it into a waveform
    tensor with torchaudio directly from the in-memory bytes.
    """
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", input_path,
        "-f", "wav",
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="ignore"))

    with io.BytesIO(proc.stdout) as buf:
        waveform, sr = torchaudio.load(buf)  # torchaudio reads file-like objects directly

    waveform = waveform.mean(dim=0)  # to mono (already mono from ffmpeg, but safe)
    return waveform  # 1D tensor, shape [num_samples]


def pad_or_trim(window: torch.Tensor, target_length: int) -> torch.Tensor:
    if window.shape[0] < target_length:
        return F.pad(window, (0, target_length - window.shape[0]))
    return window[:target_length]


# =========================================================
# PREDICTION (per 5s window, per model)
# =========================================================

def predict_probs_batch(model, windows: torch.Tensor) -> torch.Tensor:
    """
    Runs ALL sub-windows for a minute through the model in a single
    forward pass instead of one call per window. `windows` has shape
    [num_windows, samples]. Returns [num_windows, num_classes] probs.
    """
    x = windows.to(DEVICE)  # [num_windows, samples]
    with torch.no_grad():
        logits = model(x)
        probs = F.softmax(logits, dim=1)
    return probs.cpu()


def classify_minute(model, minute_waveform: torch.Tensor) -> dict:
    """
    Split one minute of audio into 5s sub-windows, stack them into a
    single batch, and classify all of them in ONE forward pass. Then
    average the probabilities across sub-windows to get one prediction
    for this minute.
    """
    num_samples = minute_waveform.shape[0]
    num_windows = max(1, round(num_samples / WINDOW_LENGTH))

    windows = torch.stack([
        pad_or_trim(
            minute_waveform[i * WINDOW_LENGTH: (i + 1) * WINDOW_LENGTH],
            WINDOW_LENGTH,
        )
        for i in range(num_windows)
    ])  # [num_windows, WINDOW_LENGTH]

    probs = predict_probs_batch(model, windows)  # [num_windows, num_classes]
    avg_probs = probs.mean(dim=0)
    confidence, pred_idx = torch.max(avg_probs, dim=0)

    # Reject to "other" when the model isn't confident it's rain or car.
    if confidence.item() < OTHER_THRESHOLD:
        predicted_label = "other"
    else:
        predicted_label = CLASSES[pred_idx.item()]

    result = {
        "prediction": predicted_label,
        "confidence": round(confidence.item() * 100, 2),
    }
    for i, cls in enumerate(CLASSES):
        result[f"{cls}_prob"] = round(avg_probs[i].item() * 100, 2)

    return result


# =========================================================
# MAIN PIPELINE
# =========================================================

def process(input_path: str, csv_path: str, model, chunk_seconds: int = CHUNK_SECONDS):
    waveform = decode_full_audio(input_path)  # whole 15 min, in RAM
    total_samples = waveform.shape[0]
    chunk_length = SAMPLE_RATE * chunk_seconds

    rows = []
    idx = 0
    start = 0
    while start < total_samples:
        end = min(start + chunk_length, total_samples)
        minute_waveform = waveform[start:end]

        row = {
            "chunk_index": idx,
            "start_sec": round(start / SAMPLE_RATE, 2),
            "end_sec": round(end / SAMPLE_RATE, 2),
        }
        row.update(classify_minute(model, minute_waveform))
        rows.append(row)

        start += chunk_length
        idx += 1

    # ---- the ONLY disk write in the whole pipeline ----
    fieldnames = list(rows[0].keys()) if rows else []
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return csv_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python classify_audio_ram.py <input_audio> <output.csv>")
        sys.exit(1)

    print("=== LOADING MODEL ===")
    model = load_wav2vec("../training/WAV2VEC2.pth")

    print("=== PROCESSING ===")
    out = process(sys.argv[1], sys.argv[2], model)
    print(f"Wrote {out}")
