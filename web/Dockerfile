FROM node:24.15.0-alpine AS build
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm test && npm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine
USER root
RUN sed -i 's|dl-cdn[.]alpinelinux[.]org|mirrors.aliyun.com|g' /etc/apk/repositories \
 && apk upgrade --no-cache zlib
COPY web/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/
RUN find /usr/share/nginx/html -type d -exec chmod 0755 {} + \
 && find /usr/share/nginx/html -type f -exec chmod 0644 {} +
USER 101
EXPOSE 8080
