import random
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

import torchaudio

from transformers import Wav2Vec2Model


# =========================================================
# CONFIG
# =========================================================

DATASET_PATH = "../dataset"

SAMPLE_RATE = 16000

BATCH_SIZE = 2
EPOCHS = 5
LEARNING_RATE = 1e-4

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

CLASSES = ["rain", "car"]

# =========================================================
# DATASET
# =========================================================

class AudioDataset(Dataset):

    def __init__(self, root_dir):

        self.samples = []

        for label_idx, label in enumerate(CLASSES):

            folder = Path(root_dir) / label

            for file in folder.glob("*"):
                self.samples.append((str(file), label_idx))

        random.shuffle(self.samples)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):

        path, label = self.samples[idx]

        waveform, sr = torchaudio.load(path)

        waveform = waveform.mean(dim=0)

        if sr != SAMPLE_RATE:

            resampler = torchaudio.transforms.Resample(
                sr,
                SAMPLE_RATE
            )

            waveform = resampler(waveform)

        # fixed 5 sec
        target_length = SAMPLE_RATE * 5

        if waveform.shape[0] < target_length:

            pad = target_length - waveform.shape[0]

            waveform = nn.functional.pad(
                waveform,
                (0, pad)
            )

        else:
            waveform = waveform[:target_length]

        return waveform, label


# =========================================================
# SIMPLE 1D CNN
# =========================================================

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
                    padding=2
                )
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

        x = self.classifier(x)

        return x


# =========================================================
# WAV2VEC2 CLASSIFIER
# =========================================================

class Wav2Vec2Classifier(nn.Module):

    def __init__(self):

        super().__init__()

        self.wav2vec = Wav2Vec2Model.from_pretrained(
            "facebook/wav2vec2-base"
        )

        self.classifier = nn.Linear(768, 2)

    def forward(self, x):

        outputs = self.wav2vec(x)

        hidden = outputs.last_hidden_state

        pooled = hidden.mean(dim=1)

        logits = self.classifier(pooled)

        return logits


# =========================================================
# MODELS
# =========================================================

models_dict = {

    "CNN1D_SMALL": CNN1D([16, 32, 64]),

    "CNN1D_DEEP": CNN1D([16, 32, 64, 128]),

    "WAV2VEC2": Wav2Vec2Classifier(),
}

# =========================================================
# TRAIN FUNCTION
# =========================================================

def train_model(model_name, model, loader):

    print(f"\n========== TRAINING {model_name} ==========")

    model = model.to(DEVICE)

    criterion = nn.CrossEntropyLoss()

    optimizer = optim.Adam(
        model.parameters(),
        lr=LEARNING_RATE
    )

    for epoch in range(EPOCHS):

        model.train()

        total_loss = 0
        correct = 0
        total = 0

        for x, y in loader:

            x = x.to(DEVICE)
            y = y.to(DEVICE)

            optimizer.zero_grad()

            outputs = model(x)

            loss = criterion(outputs, y)

            loss.backward()

            optimizer.step()

            total_loss += loss.item()

            preds = outputs.argmax(dim=1)

            correct += (preds == y).sum().item()

            total += y.size(0)

        acc = 100 * correct / total

        print(
            f"Epoch {epoch+1}/{EPOCHS} | "
            f"Loss: {total_loss:.4f} | "
            f"Accuracy: {acc:.2f}%"
        )

    torch.save(
        model.state_dict(),
        f"{model_name}.pth"
    )

    print(f"{model_name} saved.")


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    dataset = AudioDataset(DATASET_PATH)
    print(len(dataset))

    loader = DataLoader(
        dataset,
        batch_size=BATCH_SIZE,
        shuffle=True
    )

    for model_name, model in models_dict.items():

        train_model(
            model_name,
            model,
            loader
        )
