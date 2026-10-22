FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY agent ./agent
COPY web ./web
COPY data ./data
EXPOSE 8000
CMD ["uvicorn", "agent.app:app", "--host", "0.0.0.0", "--port", "8000"]
