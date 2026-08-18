"""Converte o modelo treinado de PyTorch para ONNX e valida a equivalência.

Motivo: o PyTorch pesa ~2,8 GB instalado e existe no servidor apenas para rodar
inferência. O ONNX Runtime faz o mesmo com ~25 MB — 80% de redução no tamanho
do sistema, o que viabiliza hospedagem gratuita.

A validação não é opcional: um modelo exportado que produz saídas diferentes
mudaria silenciosamente o laudo. O script compara as probabilidades dos dois
motores em entradas aleatórias e falha se divergirem além da tolerância.

Uso (na máquina de desenvolvimento, onde o PyTorch está instalado):
    .venv\\Scripts\\python scripts\\export_onnx.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.classification.deep_model import (CLASSES, MODEL_PATH,  # noqa: E402
                                                   ONNX_PATH, TARGET_LEN,
                                                   build_model)

TOLERANCIA = 1e-4          # diferença máxima aceitável nas probabilidades
N_AMOSTRAS_TESTE = 8


def main() -> int:
    import torch

    if not MODEL_PATH.exists():
        print(f"Modelo PyTorch não encontrado em {MODEL_PATH}.")
        print("Treine primeiro com: python scripts/train_ptbxl.py")
        return 1

    modelo = build_model()
    modelo.load_state_dict(torch.load(MODEL_PATH, map_location="cpu", weights_only=True))
    modelo.eval()

    exemplo = torch.randn(1, 12, TARGET_LEN, dtype=torch.float32)
    ONNX_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"Exportando {MODEL_PATH.name} → {ONNX_PATH.name} …")
    torch.onnx.export(
        modelo, exemplo, str(ONNX_PATH),
        input_names=["ecg"], output_names=["logits"],
        # Lote dinâmico: permite classificar vários registros de uma vez.
        dynamic_axes={"ecg": {0: "lote"}, "logits": {0: "lote"}},
        opset_version=17,
    )

    import onnx

    # O exportador do PyTorch grava os pesos num arquivo .onnx.data separado.
    # Reescrevemos tudo num arquivo único: um deploy que copie apenas o .onnx
    # (o caso natural) carregaria um modelo sem pesos e falharia em produção.
    modelo_onnx = onnx.load(str(ONNX_PATH), load_external_data=True)
    for extra in ONNX_PATH.parent.glob(f"{ONNX_PATH.name}.data"):
        extra.unlink()
    onnx.save(modelo_onnx, str(ONNX_PATH), save_as_external_data=False)

    conferido = onnx.load(str(ONNX_PATH))
    onnx.checker.check_model(conferido)
    externos = [i.name for i in conferido.graph.initializer
                if i.data_location == onnx.TensorProto.EXTERNAL]
    if externos:
        print(f"FALHOU: {len(externos)} peso(s) ainda em arquivo externo.")
        return 1
    print(f"  arquivo único e válido: {ONNX_PATH.stat().st_size / 1024 / 1024:.1f} MB "
          f"(PyTorch: {MODEL_PATH.stat().st_size / 1024 / 1024:.1f} MB)")

    # ---- Validação: as probabilidades dos dois motores precisam coincidir ----
    import onnxruntime as ort
    sessao = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])

    rng = np.random.default_rng(0)
    pior = 0.0
    for i in range(N_AMOSTRAS_TESTE):
        x = rng.standard_normal((1, 12, TARGET_LEN)).astype(np.float32)
        with torch.no_grad():
            p_torch = torch.sigmoid(modelo(torch.from_numpy(x))).numpy()
        p_onnx = 1.0 / (1.0 + np.exp(-sessao.run(None, {"ecg": x})[0]))
        dif = float(np.max(np.abs(p_torch - p_onnx)))
        pior = max(pior, dif)
        print(f"  amostra {i + 1}/{N_AMOSTRAS_TESTE}: diferença máxima {dif:.2e}")

    print(f"\nMaior divergência entre PyTorch e ONNX: {pior:.2e} "
          f"(tolerância {TOLERANCIA:.0e})")
    if pior > TOLERANCIA:
        print("FALHOU: as saídas divergem além do aceitável. NÃO use este ONNX —")
        print("o laudo mudaria em relação ao modelo validado.")
        return 1

    print("OK: o modelo ONNX reproduz o PyTorch dentro da tolerância.")
    print(f"\nClasses na ordem de saída: {', '.join(c for c, _ in CLASSES)}")
    print("O servidor passa a usar o ONNX automaticamente; o PyTorch deixa de ser")
    print("necessário em produção (segue exigido apenas para treinar/exportar).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
