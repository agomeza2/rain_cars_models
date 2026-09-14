import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio

from transformers import Wav2Vec2Model

# =========================================================
# CONFIG
# =========================================================

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

SAMPLE_RATE = 16000
AUDIO_DURATION = 5  # segundos
TARGET_LENGTH = SAMPLE_RATE * AUDIO_DURATION

CLASSES = ["rain", "car"]

# =========================================================
# CNN MODEL
# =========================================================

class CNN1D(nn.Module):

    def __init__(self, channels):

        super().__init__()

        layers = []

        in_channels = 1

        for out_channels in channels:

            layers.extend([
                nn.Conv1d(
                    in_channels,
                    out_channels,
                    kernel_size=5,
                    stride=2,
                    padding=2
                ),

                nn.BatchNorm1d(out_channels),

                nn.ReLU(),

                nn.MaxPool1d(2)
            ])

            in_channels = out_channels

        self.features = nn.Sequential(*layers)

        self.pool = nn.AdaptiveAvgPool1d(1)

        self.classifier = nn.Linear(channels[-1], len(CLASSES))

    def forward(self, x):

        x = x.unsqueeze(1)

        x = self.features(x)

        x = self.pool(x)

        x = x.squeeze(-1)

        return self.classifier(x)

# =========================================================
# WAV2VEC2 MODEL
# =========================================================

class Wav2Vec2Classifier(nn.Module):

    def __init__(self):

        super().__init__()

        self.wav2vec = Wav2Vec2Model.from_pretrained(
            "facebook/wav2vec2-base"
        )

        self.classifier = nn.Linear(768, len(CLASSES))

    def forward(self, x):

        outputs = self.wav2vec(x)

        hidden_states = outputs.last_hidden_state

        pooled = hidden_states.mean(dim=1)

        return self.classifier(pooled)

# =========================================================
# AUDIO PREPROCESSING
# =========================================================

def preprocess_audio(audio_path):

    waveform, sample_rate = torchaudio.load(audio_path)

    # Convertir a mono
    waveform = waveform.mean(dim=0)

    # Resample
    if sample_rate != SAMPLE_RATE:

        resampler = torchaudio.transforms.Resample(
            sample_rate,
            SAMPLE_RATE
        )

        waveform = resampler(waveform)

    # Pad o trim
    if waveform.shape[0] < TARGET_LENGTH:

        padding = TARGET_LENGTH - waveform.shape[0]

        waveform = F.pad(waveform, (0, padding))

    else:

        waveform = waveform[:TARGET_LENGTH]

    return waveform


def preprocess_wav2vec(audio_path):

    waveform = preprocess_audio(audio_path)

    return waveform.unsqueeze(0)

# =========================================================
# LOAD MODELS
# =========================================================

def load_cnn(model_path, config):

    model = CNN1D(config)

    model.load_state_dict(
        torch.load(model_path, map_location=DEVICE)
    )

    model.to(DEVICE)

    model.eval()

    return model


def load_wav2vec(model_path):

    model = Wav2Vec2Classifier()

    model.load_state_dict(
        torch.load(model_path, map_location=DEVICE)
    )

    model.to(DEVICE)

    model.eval()

    return model

# =========================================================
# PREDICTION
# =========================================================

def predict(model, audio):

    with torch.no_grad():

        audio = audio.to(DEVICE)

        logits = model(audio)

        probabilities = F.softmax(logits, dim=1)

        confidence, prediction = torch.max(
            probabilities,
            dim=1
        )

        predicted_class = CLASSES[prediction.item()]

        confidence_percent = confidence.item() * 100

        all_probabilities = {
            CLASSES[i]: f"{probabilities[0][i].item() * 100:.2f}%"
            for i in range(len(CLASSES))
        }

        return {
            "prediction": predicted_class,
            "confidence": f"{confidence_percent:.2f}%",
            "probabilities": all_probabilities
        }

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    audio_path = (
      "Lluvia_techo_1.m4a"
    )

    print("\n=== LOADING MODELS ===")

    cnn_small = load_cnn(
        "../training/CNN1D_SMALL.pth",
        [16, 32, 64]
    )

    cnn_deep = load_cnn(
        "../training/CNN1D_DEEP.pth",
        [16, 32, 64, 128]
    )

    wav2vec = load_wav2vec(
        "../training/WAV2VEC2.pth"
    )

    print("\n=== PREPROCESSING AUDIO ===")

    audio_cnn = preprocess_audio(audio_path).unsqueeze(0)

    audio_w2v = preprocess_wav2vec(audio_path)

    print("\n=== PREDICTIONS ===")

    print("\nCNN_SMALL")
    print(predict(cnn_small, audio_cnn))

    print("\nCNN_DEEP")
    print(predict(cnn_deep, audio_cnn))

    print("\nWAV2VEC2")
    print(predict(wav2vec, audio_w2v))
