# 构建阶段包含开发依赖，使用锁文件安装并在产出静态资源前执行前端单测。
FROM node:24.15.0-alpine AS build
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
# 契约测试按../../contracts读取共享合成fixture；仅复制到构建阶段，运行镜像仍只接收dist。
COPY contracts/fixtures/ /contracts/fixtures/
COPY web/ ./
RUN npm test && npm run build

# 运行阶段仅保留 Nginx 与 dist，最终回到非 root 用户，不把源码构建工具带入运行容器。
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
