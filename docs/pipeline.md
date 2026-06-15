# Pipeline de Eventos — Guardian Live Monitor

## Índice

1. [Arquitectura actual](#1-arquitectura-actual)
2. [Flujo de un evento](#2-flujo-de-un-evento)
3. [Componentes en detalle](#3-componentes-en-detalle)
4. [Agregar nuevos tipos de detección](#4-agregar-nuevos-tipos-de-detección)
5. [Escalar con RabbitMQ / Kafka](#5-escalar-con-rabbitmq--kafka)
6. [Roadmap recomendado](#6-roadmap-recomendado)

---

## 1. Arquitectura actual

```
┌──────────────┐    ┌──────────┐    ┌────────────┐    ┌───────────┐
│  Detector    │───▶│ Backend  │───▶│  Redis     │───▶│ Dashboard │
│  (OpenCV)    │    │ (FastAPI)│    │  (Pub/Sub) │    │ (WebSocket│
│              │    │          │    │            │    │  + MJPEG) │
│  MJPEG stream│   │          │    │            │    │           │
│  :8080/stream │   │          │    │            │    │           │
└──────────────┘   └────┬─────┘    └────────────┘    └───────────┘
                        │
                        ▼
                  ┌──────────┐
                  │PostgreSQL│
                  │ (eventos)│
                  └──────────┘
```

### Capas

| Capa | Tecnología | Rol |
|------|-----------|-----|
| **Captura** | OpenCV (Python) | Lee webcam, detecta movimiento, genera stream MJPEG |
| **API** | FastAPI (Python) | Recibe eventos, los persiste, los publica en Redis |
| **Mensajería** | Redis Pub/Sub | Distribuye eventos en tiempo real a los subscriptores |
| **Persistencia** | PostgreSQL 16 | Almacenamiento durable de eventos |
| **Presentación** | Nginx + HTML/JS | Dashboard web con stream MJPEG y WebSocket |

---

## 2. Flujo de un evento

### Paso a paso

```
  DETECTOR                          BACKEND                          DASHBOARD
     │                                 │                                │
     │  1. Captura frame (30 FPS)      │                                │
     │  2. Frame differencing           │                                │
     │  3. ¿motion_pixels > umbral?    │                                │
     │     │                            │                                │
     │  4. POST /api/events ──────────▶│                                │
     │     │                            │                                │
     │     │                        5. Valida payload                   │
     │     │                        6. Crea Event (SQLAlchemy)          │
     │     │                        7. INSERT en PostgreSQL             │
     │     │                        8. Publica en Redis "live_events"   │
     │     │                            │                                │
     │     │                            │  Redis Pub/Sub                │
     │     │                            │══════════════════════════════▶│
     │     │                            │                                │
     │     │                        9. Redis Worker recibe              │
     │     │                       10. Broadcast a WebSocket ─────────▶│
     │     │                            │                                │
     │     │                            │                           11. handleEvent()
     │     │                            │                           12. Crea card en feed
     │     │                            │                           13. Anima pipeline
     │     │                            │                           14. Actualiza stats
     │     │                            │                                │
     │ 201 Created ◀────────────────────│                                │
     │     │                            │                                │
```

### Trazabilidad

Cada evento tiene un **UUID único** (PK en PostgreSQL) que permite rastrearlo a través de todo el pipeline:

```
Detector → POST /api/events → PostgreSQL (id: UUID) → Redis (misma ID) → WebSocket → Dashboard
```

### Formato del evento

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "camera_id": "CAM-01",
  "event_type": "motion_detected",
  "severity": "high",
  "confidence": 0.95,
  "timestamp": "2026-06-15T01:30:00.128499Z"
}
```

---

## 3. Componentes en detalle

### 3.1 Detector (`detector/`)

**Responsabilidad**: Capturar video, detectar movimiento, servir stream MJPEG.

```
main()
├── Abrir cámara (cv2.VideoCapture)
├── Iniciar server MJPEG (threading, puerto 8080)
└── Loop de detección
    ├── Leer frame
    ├── Encode JPEG → _latest_jpeg (para MJPEG stream)
    ├── Gray + Blur
    ├── Frame differencing vs frame anterior
    ├── threshold + dilate
    ├── countNonZero → motion_pixels
    └── Si supera umbral + cooldown:
        ├── Calcular severity
        └── POST /api/events (httpx)
```

**Configuración** (variables de entorno):

| Variable | Default | Descripción |
|----------|---------|-------------|
| `SOURCE` | `webcam` | `webcam` o `video` (archivo sintético) |
| `CAMERA_ID` | `CAM-01` | Identificador de la cámara |
| `MOTION_THRESHOLD` | `5000` | Píxeles en movimiento para detonar alerta |
| `COOLDOWN_SECONDS` | `3` | Segundos entre alertas |
| `BACKEND_URL` | `http://backend:8000` | URL del backend API |

### 3.2 Backend (`backend/`)

**Responsabilidad**: API REST + WebSocket + worker de Redis.

```
uvicorn app.main:app
├── POST /api/events → create_event()
│   ├── Valida EventPayload (Pydantic)
│   ├── db.add(Event) + db.commit()
│   ├── db.refresh(event) → obtiene id + timestamp
│   ├── EventResponse.model_dump(mode="json")
│   ├── publish_event(redis_conn, event_dict)
│   └── return EventResponse (201)
│
├── WS /ws/events → websocket_endpoint()
│   └── manager.connect(websocket)
│       └── Espera mensajes (mantiene conexión viva)
│
└── redis_worker() (background task)
    ├── get_redis() → conexión compartida singleton
    ├── pubsub.subscribe("live_events")
    └── async for message in pubsub.listen()
        └── manager.broadcast(raw)
```

**Modelo de datos** (PostgreSQL):

```sql
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id VARCHAR(50) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(10) NOT NULL,    -- low | medium | high
    confidence FLOAT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT now()
);
```

### 3.3 Redis

**Rol**: Pub/Sub para distribución en tiempo real.

- Canal: `live_events`
- Mensaje: JSON string del evento
- Conexión: compartida (singleton), con `health_check_interval=15s`, `socket_timeout=30s`

### 3.4 Dashboard (`dashboard/`)

**Responsabilidad**: Interfaz web.

```
Nginx
├── / → index.html (Tailwind CSS)
├── /app.js → GuardianMonitor class
│   ├── WebSocket → ws://backend:8000/ws/events
│   ├── handleEvent() → renderiza card + anima pipeline
│   └── animatePipeline() → secuencia visual 📡→📥→🗄️→⚡→🔌
└── /stream → proxy_pass → detector:8080/stream (MJPEG)
```

---

## 4. Agregar nuevos tipos de detección

La arquitectura actual está diseñada para **múltiples detectores**. Cada detector es un contenedor independiente que:

1. Procesa frames (OpenCV, YOLO, modelo ML, etc.)
2. Publica eventos al mismo backend (`POST /api/events`)
3. El campo `event_type` diferencia cada tipo

### 4.1 Ejemplo: Detección de vehículos (autos)

```
detector-vehiculos/
├── Dockerfile
├── requirements.txt  # ultralytics (YOLO), opencv, httpx
└── detect.py
```

```python
# detect.py  —  pseudocódigo
import cv2
from ultralytics import YOLO
import httpx

model = YOLO("yolo11n.pt")  # pre-entrenado con 80 clases (car, truck, etc.)

cap = cv2.VideoCapture(0)
for frame in cap:
    results = model(frame)
    for det in results[0].boxes:
        if det.cls in [2, 3, 5, 7]:  # car, motorcycle, bus, truck
            send_event({
                "camera_id": "CAM-01",
                "event_type": "vehicle_detected",     # ← nuevo tipo
                "severity": "medium",
                "confidence": float(det.conf),
                "metadata": {                          # ← datos extra opcionales
                    "vehicle_class": model.names[int(det.cls)],
                    "bbox": det.xyxy.tolist()
                }
            })
```

### 4.2 Ejemplo: Detección de cuchillos / armas

```
detector-armas/
├── Dockerfile
├── requirements.txt  # torch, opencv, httpx
└── detect.py
```

```python
# Usar modelo especializado (ej: fine-tune de YOLO en dataset de armas)
model = torch.hub.load("ultralytics/yolov5", "custom", path="weapon-detection.pt")

for frame in cap:
    results = model(frame)
    if len(results.xyxy[0]) > 0:
        send_event({
            "camera_id": "CAM-01",
            "event_type": "weapon_detected",
            "severity": "high",          # severidad máxima
            "confidence": float(results.xyxy[0][0][4]),
        })
```

### 4.3 Ejemplo: Reconocimiento de patentes (ALPR)

```
detector-patentes/
├── Dockerfile
├── requirements.txt  # openalpr, opencv, httpx
└── detect.py
```

```python
# Opción A: OpenALPR
from openalpr import Alpr
alpr = Alpr("us", "/etc/openalpr/openalpr.conf", "/usr/share/openalpr/runtime_data")

# Opción B: YOLO + OCR (más moderno)
# 1. YOLO detecta placas → recorta región
# 2. OCR (Tesseract / PaddleOCR) extrae texto
for frame in cap:
    plates = detect_plates_yolo(frame)  # bounding boxes de placas
    for x1, y1, x2, y2 in plates:
        plate_img = frame[y1:y2, x1:x2]
        text = pytesseract.image_to_string(plate_img)
        send_event({
            "camera_id": "CAM-01",
            "event_type": "plate_detected",
            "severity": "medium",
            "confidence": 0.92,
            "metadata": {"plate": text.strip()}
        })
```

### 4.4 Arquitectura multi-detector

```
                     ┌──────────────────┐
                     │  detector-motion  │  ← actual (frame differencing)
                     │  event_type:      │
                     │  motion_detected  │
                     └────────┬─────────┘
                              │ POST /api/events
                              ▼
┌──────────────────┐   ┌──────────────┐   ┌──────────────────┐
│  detector-autos  │──▶│   Backend    │◀──│  detector-armas  │
│  event_type:     │   │   (FastAPI)  │   │  event_type:     │
│  vehicle_detected│   └──────┬───────┘   │  weapon_detected  │
└──────────────────┘          │           └──────────────────┘
                              │
                     ┌────────┴────────┐
                     │   PostgreSQL    │
                     │  events TABLE   │
                     │                 │
                     │ event_type INDEX│
                     └─────────────────┘
```

Cada detector es **independiente**:
- Puede tener su propia cámara o compartir stream MJPEG
- Se escala horizontalmente (más réplicas = más FPS procesados)
- Cada `event_type` se filtra fácilmente en dashboard y queries

---

## 5. Escalar con RabbitMQ / Kafka

### 5.1 ¿Por qué migrar de Redis Pub/Sub?

Redis Pub/Sub funciona bien para el MVP actual, pero tiene limitaciones:

| Aspecto | Redis Pub/Sub | RabbitMQ | Kafka |
|---------|--------------|----------|-------|
| **Persistencia** | ❌ No guarda mensajes | ✅ Colas durables | ✅ Log inmutable |
| **Replay** | ❌ No se puede | ✅ Mensajes no acknowledeados se reenvían | ✅ Offset rebobinable |
| **Routing** | ❌ Un solo canal | ✅ Topics, headers, direct | ✅ Topics con particiones |
| **Garantía** | ❌ At-most-once | ✅ At-least-once | ✅ Exactly-once (conectores) |
| **Throughput** | Medio (~10k msg/s) | Medio (~50k msg/s) | Alto (~1M msg/s) |
| **Retención** | ❌ Solo mientras hay subscriptores | ✅ Hasta ACK o TTL | ✅ Configurable (días/semanas) |

### 5.2 Cuándo migrar

**Migrar a RabbitMQ cuando:**
- Necesitás **garantía de entrega** (si un detector publica un evento y el backend está caído, no se pierde)
- Tenés **múltiples consumidores** con diferentes capacidades (ej: dashboard + archivado + ML)
- Necesitás **colas de retry** para eventos fallidos

**Migrar a Kafka cuando:**
- El throughput supera ~10,000 eventos/segundo
- Necesitás **reprocesar histórico** (recalcular métricas, re-alimentar modelos)
- Querés **stream processing** (KSQL, Spark Streaming, Flink)
- Tenés múltiples equipos/consumidores independientes

### 5.3 Arquitectura con RabbitMQ

```
                    ┌──────────┐
                    │ Detector │
                    └────┬─────┘
                         │ POST /api/events
                         ▼
                   ┌──────────┐
                   │  Backend │──▶ PostgreSQL
                   │ (FastAPI)│
                   └────┬─────┘
                        │ publish(exchange="events", routing_key="motion.*")
                        ▼
                  ┌──────────────┐
                  │   RabbitMQ   │
                  │ exchange:    │
                  │  guardian    │
                  └──────┬───────┘
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
    ┌────────────┐ ┌──────────┐ ┌──────────┐
    │ Queue:     │ │ Queue:   │ │ Queue:   │
    │ dashboard  │ │ archive  │ │ ml-pipe  │
    │ (WebSocket)│ │ (S3/disk)│ │ (modelo) │
    └────────────┘ └──────────┘ └──────────┘
```

**Docker Compose con RabbitMQ:**

```yaml
services:
  rabbitmq:
    image: rabbitmq:3-management-alpine
    ports:
      - "5672:5672"   # AMQP
      - "15672:15672" # Management UI

  backend:
    depends_on:
      - rabbitmq
    # RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
```

**Código backend con RabbitMQ (aio-pika):**

```python
import aio_pika

async def publish_event(event_dict):
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.get_exchange("guardian.events")
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(event_dict).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=f"event.{event_dict['event_type']}",
        )
```

### 5.4 Arquitectura con Kafka

```
                    ┌──────────┐
                    │ Detector │
                    └────┬─────┘
                         │ POST /api/events
                         ▼
                   ┌──────────┐
                   │  Backend │──▶ PostgreSQL
                   │ (FastAPI)│
                   └────┬─────┘
                        │ produce(topic="guardian-events")
                        ▼
                  ┌──────────────┐
                  │    Kafka     │
                  │  topic:      │
                  │ guardian-    │
                  │ events (3    │
                  │ partitions)  │
                  └──────┬───────┘
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
    ┌────────────┐ ┌──────────┐ ┌──────────┐
    │ Consumer:  │ │Consumer: │ │Consumer: │
    │ dashboard  │ │ archive  │ │   ML     │
    │ (group:    │ │(group:   │ │ (group:  │
    │  live)     │ │  data)   │ │  models) │
    └────────────┘ └──────────┘ └──────────┘
```

**Docker Compose con Kafka:**

```yaml
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:latest
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181

  kafka:
    image: confluentinc/cp-kafka:latest
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
```

**Código backend con Kafka (aiokafka):**

```python
from aiokafka import AIOKafkaProducer

producer = AIOKafkaProducer(
    bootstrap_servers=settings.kafka_url,
    acks="all",                     # esperar confirmación de todos los brokers
    compression_type="gzip",
)

async def publish_event(event_dict):
    await producer.send(
        topic="guardian-events",
        key=event_dict["event_type"].encode(),  # partición por tipo
        value=json.dumps(event_dict).encode(),
    )

# startup
await producer.start()

# shutdown
await producer.stop()
```

### 5.5 Estrategia de migración gradual

```
Fase 1 (hoy):    Redis Pub/Sub
                 │
Fase 2:          RabbitMQ + Redis (coexistencia)
                 │   RabbitMQ para almacenamiento durable
                 │   Redis para live dashboard (baja latencia)
                 │
Fase 3:          Kafka (reemplaza ambos)
                 │   Un solo sistema para todo el pipeline
                 │   Retención configurable para replay
                 │   Particionado por event_type
```

La migración no requiere cambiar los detectores ni el frontend. Solo el backend cambia su mecanismo de publicación/subscripción internamente.

---

## 6. Roadmap recomendado

```mermaid
gantt
    title Guardian Live Monitor — Roadmap
    dateFormat  YYYY-MM-DD
    
    section Pipeline actual
    MVP con Redis Pub/Sub        :done, 2026-06-01, 2026-06-15
    Estabilizar Redis connection  :done, 2026-06-15, 2026-06-16

    section Nuevos detectores
    Detector de vehículos (YOLO) :active, 2026-06-20, 2026-07-10
    Detector de armas/cuchillos  :        2026-07-10, 2026-07-30
    Reconocimiento de patentes   :        2026-08-01, 2026-08-20

    section Escalamiento
    Migrar a RabbitMQ            :        2026-08-15, 2026-09-01
    Kafka evaluation             :        2026-09-01, 2026-09-15
```

---

## Referencias

- [OpenCV Motion Detection](https://docs.opencv.org/4.x/db/de9/tutorial_py_video_basics.html)
- [Ultralytics YOLO11](https://docs.ultralytics.com/)
- [RabbitMQ Tutorial (Python)](https://www.rabbitmq.com/tutorials/tutorial-one-python.html)
- [Kafka + FastAPI](https://github.com/aiokafka/aiokafka)
- [Apache Kafka Documentation](https://kafka.apache.org/documentation/)
