FROM python:3.12-slim
WORKDIR /app
COPY minidrop /app/minidrop
RUN mkdir -p /app/data
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "minidrop.server"]
