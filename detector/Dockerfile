FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias del sistema para OpenCV (headless)
# libgl1-mesa-glx fue reemplazado por libgl1 en Debian Bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "detector.py"]
