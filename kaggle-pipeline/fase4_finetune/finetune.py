"""
Fase 4: fine-tune del CNN-DEEP con los recortes revisados del lote actual
(viene como dataset_sources, montado por el orquestador antes del push).
Carga el .pth de la versión actual del Kaggle Model y guarda un .pth nuevo
en /kaggle/working — el orquestador lo toma de ahí y crea la próxima
versión del modelo.
"""

from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
import torchaudio
from torch.utils.data import DataLoader, Dataset

SAMPLE_RATE = 44000
CLASSES = ["rain", "car"]
EPOCHS = 3
LR = 1e-4
BATCH_SIZE = 8
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


class RecortesDataset(Dataset):
    def __init__(self, root_dir):
        self.samples = []
        for f in Path(root_dir).glob("*.wav"):
            label_str = f.stem.split("_")[-1]
            if label_str not in CLASSES:
                continue
            self.samples.append((str(f), CLASSES.index(label_str)))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        wav, sr = torchaudio.load(path)
        wav = wav.mean(dim=0)
        if sr != SAMPLE_RATE:
            wav = torchaudio.transforms.Resample(sr, SAMPLE_RATE)(wav)
        return wav, label


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


# el input de recortes es el único dataset_source de esta fase
recortes_dir = next(Path("/kaggle/input").glob("recortes-lote*"))
MODEL_PATH = next(Path("/kaggle/input").rglob("*.pth"), None)

model = CNN1D([16, 32, 64, 128])
model.load_state_dict(torch.load(str(MODEL_PATH), map_location="cpu"))
model.to(DEVICE)

ds = RecortesDataset(recortes_dir)
loader = DataLoader(ds, batch_size=BATCH_SIZE, shuffle=True)
print(f"Fine-tuning con {len(ds)} muestras")

criterion = nn.CrossEntropyLoss()
optimizer = optim.Adam(model.parameters(), lr=LR)

for epoch in range(EPOCHS):
    model.train()
    total_loss, correct, total = 0, 0, 0
    for x, y in loader:
        x, y = x.to(DEVICE), y.to(DEVICE)
        optimizer.zero_grad()
        out = model(x)
        loss = criterion(out, y)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        correct += (out.argmax(1) == y).sum().item()
        total += y.size(0)
    print(f"Epoch {epoch+1}/{EPOCHS} - loss {total_loss:.4f} - acc {100*correct/max(total,1):.2f}%")

torch.save(model.state_dict(), "/kaggle/working/CNN1D_DEEP.pth")
print("Guardado /kaggle/working/CNN1D_DEEP.pth")