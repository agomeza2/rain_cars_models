import torch
import torch.nn as nn
import torchaudio
import torch.nn.functional as F

from transformers import Wav2Vec2Model

# =========================================================
# CONFIG
# =========================================================

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
SAMPLE_RATE = 16000
CLASSES = ["rain", "car"]

# =========================================================
# MODELOS (MISMO QUE TRAINING)
# =========================================================

class CNN1D(nn.Module):

    def __init__(self, channels):

        super().__init__()

        layers = []

        in_ch = 1

        for ch in channels:

            layers.append(
                nn.Conv1d(in_ch, ch, kernel_size=5, stride=2, padding=2)
            )

            layers.append(nn.BatchNorm1d(ch))
            layers.append(nn.ReLU())
            layers.append(nn.MaxPool1d(2))

            in_ch = ch

        self.features = nn.Sequential(*layers)

        self.pool = nn.AdaptiveAvgPool1d(1)

        self.classifier = nn.Linear(channels[-1], 2)

    def forward(self, x):

        x = x.unsqueeze(1)

        x = self.features(x)

        x = self.pool(x)

        x = x.squeeze(-1)

        return self.classifier(x)


class Wav2Vec2Classifier(nn.Module):

    def __init__(self):

        super().__init__()

        self.wav2vec = Wav2Vec2Model.from_pretrained(
            "facebook/wav2vec2-base"
        )

        self.classifier = nn.Linear(768, 2)

    def forward(self, x):

        out = self.wav2vec(x)

        hidden = out.last_hidden_state

        pooled = hidden.mean(dim=1)

        return self.classifier(pooled)

# =========================================================
# AUDIO PREPROCESS
# =========================================================

def preprocess_audio(path):

    waveform, sr = torchaudio.load(path)

    waveform = waveform.mean(dim=0)

    if sr != SAMPLE_RATE:

        resampler = torchaudio.transforms.Resample(sr, SAMPLE_RATE)

        waveform = resampler(waveform)

    target_length = SAMPLE_RATE * 5

    if waveform.shape[0] < target_length:

        pad = target_length - waveform.shape[0]

        waveform = F.pad(waveform, (0, pad))

    else:

        waveform = waveform[:target_length]

    return waveform


def preprocess_wav2vec(path):

    waveform = preprocess_audio(path)

    return waveform.unsqueeze(0)

# =========================================================
# LOAD MODELS
# =========================================================

def load_cnn(model_path, config):

    model = CNN1D(config)

    model.load_state_dict(torch.load(model_path, map_location=DEVICE))

    model.to(DEVICE)

    model.eval()

    return model


def load_wav2vec(model_path):

    model = Wav2Vec2Classifier()

    model.load_state_dict(torch.load(model_path, map_location=DEVICE))

    model.to(DEVICE)

    model.eval()

    return model

# =========================================================
# PREDICT
# =========================================================

def predict_cnn(model, audio):

    with torch.no_grad():

        audio = audio.unsqueeze(0).to(DEVICE)

        out = model(audio)

        pred = torch.argmax(out, dim=1).item()

    return CLASSES[pred]


def predict_wav2vec(model, audio):

    with torch.no_grad():

        audio = audio.to(DEVICE)

        out = model(audio)

        pred = torch.argmax(out, dim=1).item()

    return CLASSES[pred]

# =========================================================
# MAIN TEST
# =========================================================

if __name__ == "__main__":

    audio_path = "test.wav"

    print("\n=== LOADING MODELS ===")

    cnn_small = load_cnn("../train/CNN1D_SMALL.pth", [16, 32, 64])

    cnn_deep = load_cnn("../train/CNN1D_DEEP.pth", [16, 32, 64, 128])

    wav2vec = load_wav2vec("../train/WAV2VEC2.pth")

    # AUDIO

    audio = preprocess_audio(audio_path)

    audio_w2v = preprocess_wav2vec(audio_path)

    # PREDICTIONS

    print("\n=== PREDICTIONS ===")

    print("CNN_SMALL :", predict_cnn(cnn_small, audio))

    print("CNN_DEEP  :", predict_cnn(cnn_deep, audio))

    print("WAV2VEC2  :", predict_wav2vec(wav2vec, audio_w2v))
