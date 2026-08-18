"""Carregadores de sinal digital de ECG.

Formatos suportados:
- CSV/TXT: colunas = derivações (cabeçalho opcional com nomes), valores em mV.
- WFDB: arquivo .zip contendo o par .hea/.dat (padrão PhysioNet).
"""
from __future__ import annotations

import io
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

STANDARD_LEADS = ["I", "II", "III", "aVR", "aVL", "aVF",
                  "V1", "V2", "V3", "V4", "V5", "V6"]


@dataclass
class ECGRecord:
    """Sinal de ECG em mV, formato (n_amostras, n_derivações)."""
    signal: np.ndarray
    sampling_rate: float
    lead_names: list[str] = field(default_factory=list)
    source_format: str = "desconhecido"
    notes: list[str] = field(default_factory=list)

    @property
    def n_leads(self) -> int:
        return self.signal.shape[1]

    @property
    def duration_s(self) -> float:
        return self.signal.shape[0] / self.sampling_rate

    def lead(self, name: str) -> np.ndarray | None:
        """Busca a derivação ignorando maiúsculas/minúsculas.

        Necessário porque os padrões divergem: o PTB-XL grava 'AVF'/'AVR',
        enquanto a convenção clínica escreve 'aVF'/'aVR'. Comparação sensível
        a caixa fazia o cálculo do eixo elétrico falhar silenciosamente em
        todo o dataset.
        """
        target = name.strip().lower()
        for i, n in enumerate(self.lead_names):
            if str(n).strip().lower() == target:
                return self.signal[:, i]
        return None

    def _completa(self, i: int) -> bool:
        return bool(np.isfinite(self.signal[:, i]).all())

    def best_rhythm_lead(self) -> tuple[str, np.ndarray]:
        """Derivação para análise de ritmo.

        Exige cobertura do registro inteiro: num laudo em imagem existe uma
        derivação "II" com apenas 2,5 s (linha 2, coluna 1) além da tira de
        ritmo, que cobre os 10 s. Escolher a primeira pelo nome deixaria a
        análise de ritmo com um oitavo dos batimentos.
        """
        for name in ("ritmo", "II", "MLII"):
            for i, n in enumerate(self.lead_names):
                if str(n).strip().lower() == name.lower() and self._completa(i):
                    return str(n), self.signal[:, i]

        completas = [i for i in range(self.signal.shape[1]) if self._completa(i)]
        if not completas:
            idx = int(np.nanargmax(np.nanmax(self.signal, axis=0)
                                   - np.nanmin(self.signal, axis=0)))
            return self.lead_names[idx], self.signal[:, idx]
        idx = max(completas, key=lambda i: float(np.ptp(self.signal[:, i])))
        return self.lead_names[idx], self.signal[:, idx]


def load_csv(data: bytes, sampling_rate: float = 500.0) -> ECGRecord:
    text = data.decode("utf-8-sig", errors="replace")
    delimiter = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
    text = text.replace("\t", delimiter)

    lines = [ln for ln in text.splitlines() if ln.strip()]
    header: list[str] | None = None
    first = lines[0].split(delimiter)
    try:
        [float(x) for x in first if x.strip()]
    except ValueError:
        header = [h.strip() for h in first if h.strip()]
        lines = lines[1:]

    rows = []
    for ln in lines:
        try:
            rows.append([float(x.replace(",", ".")) if delimiter != "," else float(x)
                         for x in ln.split(delimiter) if x.strip()])
        except ValueError:
            continue
    if not rows:
        raise ValueError("CSV sem dados numéricos legíveis.")

    n_cols = max(len(r) for r in rows)
    rows = [r for r in rows if len(r) == n_cols]
    signal = np.asarray(rows, dtype=float)
    if signal.shape[0] < signal.shape[1]:  # provavelmente transposto
        signal = signal.T

    notes = []
    # Heurística de unidade: ECG em mV raramente passa de ~10 mV
    p2p = float(np.median(np.ptp(signal, axis=0)))
    if p2p > 50:
        signal = signal / 1000.0
        notes.append("Amplitudes altas detectadas: valores interpretados como µV e convertidos para mV.")

    lead_names = header if header and len(header) == signal.shape[1] else \
        (STANDARD_LEADS[: signal.shape[1]] if signal.shape[1] <= 12
         else [f"CH{i+1}" for i in range(signal.shape[1])])
    if signal.shape[1] == 1 and not header:
        lead_names = ["II"]
        notes.append("Sinal de derivação única: assumido como derivação de ritmo (II).")

    return ECGRecord(signal=signal, sampling_rate=sampling_rate,
                     lead_names=lead_names, source_format="csv", notes=notes)


def load_wfdb_zip(data: bytes) -> ECGRecord:
    import wfdb

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(tmp)
        hea = list(Path(tmp).rglob("*.hea"))
        if not hea:
            raise ValueError("O .zip não contém um cabeçalho WFDB (.hea).")
        record = wfdb.rdrecord(str(hea[0].with_suffix("")))

    signal = np.asarray(record.p_signal, dtype=float)
    units = [u.strip().lower() if u else "mv" for u in (record.units or [])]
    notes = []
    for i, u in enumerate(units):
        if "uv" in u or "µv" in u or "μv" in u:
            signal[:, i] /= 1000.0
            notes.append(f"Canal {i + 1} convertido de µV para mV.")
        elif u in ("v", "volt", "volts"):
            signal[:, i] *= 1000.0
            notes.append(f"Canal {i + 1} convertido de V para mV.")
        elif u not in ("mv", "millivolt", "millivolts", ""):
            notes.append(f"Canal {i + 1}: unidade '{u}' não reconhecida; "
                         "valores assumidos em mV — conferir a amplitude do traçado.")
    lead_names = [str(n) for n in (record.sig_name or [])] or STANDARD_LEADS[: signal.shape[1]]
    return ECGRecord(signal=signal, sampling_rate=float(record.fs),
                     lead_names=lead_names, source_format="wfdb", notes=notes)


def load_digital(filename: str, data: bytes, sampling_rate: float = 500.0) -> ECGRecord:
    suffix = Path(filename).suffix.lower()
    if suffix == ".zip":
        return load_wfdb_zip(data)
    if suffix in (".csv", ".txt"):
        return load_csv(data, sampling_rate=sampling_rate)
    raise ValueError(f"Formato de sinal digital não suportado: {suffix}")
