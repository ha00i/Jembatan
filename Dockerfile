FROM python:3.12-alpine
RUN apk add --no-cache git docker-cli docker-cli-compose \
 && git config --global --add safe.directory '*'
WORKDIR /app
COPY bridge.py index.html ./
CMD ["python", "bridge.py"]
