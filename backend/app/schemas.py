"""Modelos de dados da API (Pydantic)."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class Measurements(BaseModel):
    heart_rate_bpm: Optional[float] = None
    rr_mean_ms: Optional[float] = None
    rr_cv: Optional[float] = Field(None, description="Coeficiente de variação dos intervalos RR")
    rmssd_ratio: Optional[float] = Field(
        None, description="RMSSD dos RR normalizado pelo RR médio (irregularidade batimento-a-batimento)")
    pr_ms: Optional[float] = None
    qrs_ms: Optional[float] = None
    qt_ms: Optional[float] = None
    qtc_bazett_ms: Optional[float] = None
    qtc_fridericia_ms: Optional[float] = None
    axis_degrees: Optional[float] = None
    sokolow_lyon_mv: Optional[float] = None
    p_wave_present: Optional[bool] = None
    n_beats: Optional[int] = None
    duration_s: Optional[float] = None
    sampling_rate_hz: Optional[float] = None


class Finding(BaseModel):
    code: str
    label: str
    severity: str = Field(description="normal | limitrofe | anormal | critico")
    criteria: str
    detail: str = ""


class DeepPrediction(BaseModel):
    label: str
    code: str
    probability: float


class SignalPreview(BaseModel):
    lead_name: str
    sampling_rate_hz: float
    samples: list[float]


class AnalysisResult(BaseModel):
    analysis_id: str
    created_at: str
    engine_version: str
    source: dict
    quality: dict
    measurements: Measurements
    findings: list[Finding]
    deep_learning: Optional[list[DeepPrediction]] = None
    summary: str
    disclaimer: str
    preview: Optional[list[SignalPreview]] = None
