FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY minidrop /app/minidrop
COPY --from=web /minidrop/web /app/minidrop/web
RUN mkdir -p /app/data
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "minidrop.server"]
