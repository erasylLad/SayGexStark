FROM python:3.11-slim

# Принудительно отключаем буферизацию логов Python внутри Docker
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]