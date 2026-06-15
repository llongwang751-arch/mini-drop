FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY minidrop /app/minidrop
COPY --from=web /minidrop/web /app/minidrop/web
RUN mkdir -p /app/data
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "minidrop.server:app", "--host", "0.0.0.0", "--port", "8080"]
