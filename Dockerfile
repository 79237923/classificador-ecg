# Imagem de produção do CardioLaudo.
#
# Usa apenas requirements.txt (sem PyTorch): a inferência roda por ONNX Runtime,
# o que mantém a imagem em ~1 GB em vez dos ~4 GB que o PyTorch exigiria — e é o
# que permite caber nas camadas gratuitas de hospedagem.

FROM python:3.12-slim

# Dependências de sistema do OpenCV headless e do PyMuPDF. libgl/libglib são
# exigidas mesmo pela variante headless para carregar os binários.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Camada separada para as dependências: só é reconstruída quando o
# requirements.txt muda, o que torna os deploys seguintes bem mais rápidos.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY models/ ./models/

# Diretório dos dados (banco cifrado e trilha de auditoria). Em hospedagem com
# disco efêmero ele é recriado a cada reinício — por isso a conta de
# demonstração é semeada na inicialização (ver backend/app/seed.py).
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CARDIOLAUDO_ENV=producao

EXPOSE 8000

# A porta vem do ambiente: Render, Koyeb e similares atribuem uma porta própria.
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
