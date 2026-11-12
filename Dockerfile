FROM python:3.12-alpine
RUN apk add --no-cache git docker-cli docker-cli-compose openssh-client \
 && git config --global --add safe.directory '*'
WORKDIR /app
COPY bridge.py branch_manager.py docker_control.py qa_jobs.py index.html ./
COPY assets/ ./assets/
CMD ["python", "bridge.py"]
