FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# editable install so sca.config.ROOT resolves to /app
RUN pip install --no-cache-dir -e .
COPY sql ./sql
COPY flows ./flows
ENV PYTHONUNBUFFERED=1
# flows/ is on the path so `from pipeline import ...` works; sca is installed as a package
ENV PYTHONPATH=/app/flows
CMD ["python", "flows/serve.py"]
