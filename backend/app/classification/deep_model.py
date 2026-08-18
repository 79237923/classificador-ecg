"""Classificador por deep learning (opcional).

Arquitetura: ResNet 1D sobre 12 derivações, 10 s @ 100 Hz, multirrótulo nas
5 superclasses do PTB-XL: NORM, MI, STTC, CD, HYP.

**Inferência por ONNX Runtime, não por PyTorch.** O PyTorch pesa ~2,8 GB
instalado e no servidor existiria só para rodar inferência; o ONNX Runtime faz
o mesmo com ~25 MB. Isso reduz o sistema em 80% e viabiliza hospedagem gratuita.
O PyTorch segue necessário apenas para TREINAR (`scripts/train_ptbxl.py`) e
EXPORTAR (`scripts/export_onnx.py`) — nunca em produção.

Sem o arquivo `models/ptbxl_resnet.onnx`, a API funciona normalmente com o motor
de critérios clínicos, que é independente e responde por todas as medidas e
achados do laudo.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

_MODELS = Path(__file__).resolve().parents[3] / "models"
MODEL_PATH = _MODELS / "ptbxl_resnet.pt"      # pesos de treino (só p/ exportar)
ONNX_PATH = _MODELS / "ptbxl_resnet.onnx"     # modelo usado em produção
TARGET_FS = 100
TARGET_LEN = 1000  # 10 s

CLASSES = [
    ("NORM", "ECG normal (padrão DL)"),
    ("MI", "Padrão de infarto do miocárdio"),
    ("STTC", "Alteração de ST/T"),
    ("CD", "Distúrbio de condução"),
    ("HYP", "Padrão de hipertrofia"),
]

_sessao = None


def build_model():
    import torch.nn as nn

    class Block(nn.Module):
        def __init__(self, cin, cout, stride=1):
            super().__init__()
            self.conv1 = nn.Conv1d(cin, cout, 7, stride=stride, padding=3, bias=False)
            self.bn1 = nn.BatchNorm1d(cout)
            self.conv2 = nn.Conv1d(cout, cout, 7, padding=3, bias=False)
            self.bn2 = nn.BatchNorm1d(cout)
            self.relu = nn.ReLU(inplace=True)
            self.down = (nn.Sequential(nn.Conv1d(cin, cout, 1, stride=stride, bias=False),
                                       nn.BatchNorm1d(cout))
                         if (stride != 1 or cin != cout) else nn.Identity())

        def forward(self, x):
            out = self.relu(self.bn1(self.conv1(x)))
            out = self.bn2(self.conv2(out))
            return self.relu(out + self.down(x))

    class ECGResNet(nn.Module):
        def __init__(self, n_classes=len(CLASSES)):
            super().__init__()
            self.stem = nn.Sequential(
                nn.Conv1d(12, 64, 15, stride=2, padding=7, bias=False),
                nn.BatchNorm1d(64), nn.ReLU(inplace=True),
                nn.MaxPool1d(3, stride=2, padding=1))
            self.layers = nn.Sequential(
                Block(64, 64), Block(64, 128, 2), Block(128, 128),
                Block(128, 256, 2), Block(256, 256))
            self.head = nn.Sequential(
                nn.AdaptiveAvgPool1d(1), nn.Flatten(),
                nn.Dropout(0.3), nn.Linear(256, n_classes))

        def forward(self, x):
            return self.head(self.layers(self.stem(x)))

    return ECGResNet()


def is_available() -> bool:
    """O classificador só entra em cena se houver modelo ONNX e runtime."""
    if not ONNX_PATH.exists():
        return False
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _load():
    """Sessão de inferência, criada uma vez e reaproveitada.

    Uma thread por sessão: o servidor atende poucas análises simultâneas e cada
    inferência é curta, então paralelismo interno só disputaria CPU com o
    processamento de sinal, que é o gargalo real.
    """
    global _sessao
    if _sessao is None:
        import onnxruntime as ort
        opcoes = ort.SessionOptions()
        opcoes.intra_op_num_threads = 1
        opcoes.log_severity_level = 3          # silencia avisos de rotina
        _sessao = ort.InferenceSession(str(ONNX_PATH), opcoes,
                                       providers=["CPUExecutionProvider"])
    return _sessao


# Ordem em que o modelo foi treinado (derivações do PTB-XL).
ORDEM_TREINO = ["I", "II", "III", "aVR", "aVL", "aVF",
                "V1", "V2", "V3", "V4", "V5", "V6"]


def predict(signal: np.ndarray, sampling_rate: float,
            lead_names: list[str]) -> list[dict] | None:
    """Retorna probabilidades por superclasse, ou None se indisponível.

    Exige as 12 derivações padrão, completas e simultâneas, na ordem de treino.
    Reordena pelo nome (ignorando caixa) para aceitar qualquer arranjo de
    colunas. Recusa sinal com derivações parciais (NaN) — caso das imagens, em
    que cada derivação cobre só 2,5 s: o modelo foi treinado em 10 s contínuos
    e produziria lixo.
    """
    if not is_available() or signal.shape[1] < 12:
        return None
    from scipy.signal import resample

    idx = {str(n).strip().lower(): i for i, n in enumerate(lead_names)}
    if not all(l.lower() in idx for l in ORDEM_TREINO):
        return None
    cols = [idx[l.lower()] for l in ORDEM_TREINO]
    x = signal[:, cols].astype(np.float32)
    if not np.isfinite(x).all():
        return None

    n_target = int(round(x.shape[0] * TARGET_FS / sampling_rate))
    x = resample(x, n_target, axis=0)
    if x.shape[0] >= TARGET_LEN:
        x = x[:TARGET_LEN]
    else:
        x = np.pad(x, ((0, TARGET_LEN - x.shape[0]), (0, 0)))
    x = (x - x.mean(axis=0)) / (x.std(axis=0) + 1e-6)

    # (1, 12, 1000): lote de um, derivações no eixo do canal.
    entrada = np.ascontiguousarray(x.T[None, :, :], dtype=np.float32)
    try:
        logits = _load().run(None, {"ecg": entrada})[0][0]
    except Exception:
        # Inferência é acessória: uma falha aqui não pode derrubar o laudo, que
        # vive do motor de critérios clínicos.
        return None
    probs = 1.0 / (1.0 + np.exp(-logits))     # sigmoide (saída multirrótulo)

    return [{"code": code, "label": label, "probability": float(p)}
            for (code, label), p in zip(CLASSES, probs)]
