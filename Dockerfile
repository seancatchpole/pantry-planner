FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py mcp_server.py seed_data.py .
COPY static/ static/
ENV PANTRY_DB=/data/pantry.db
VOLUME /data
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
