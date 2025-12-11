FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kube.py .
COPY main.py .
COPY templates/ ./templates/
COPY static/ ./static/
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "443", "--ssl-keyfile", "/etc/tls/tls.key", "--ssl-certfile", "/etc/tls/tls.crt"]
