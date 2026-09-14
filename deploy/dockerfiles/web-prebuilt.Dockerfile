# Publish a locally tested web/dist without installing dependencies on Control.
# Build context must be web/dist; pin WEB_RUNTIME_IMAGE to the running Web image
# digest or a release-specific rollback tag. Its nginx runtime and old hashed
# assets are retained so already-open browser tabs can finish lazy imports.
ARG WEB_RUNTIME_IMAGE
FROM ${WEB_RUNTIME_IMAGE}
COPY . /usr/share/nginx/html/
