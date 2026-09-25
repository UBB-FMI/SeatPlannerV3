FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && groupadd --gid 10001 seatplan && useradd --uid 10001 --gid 10001 --no-create-home seatplan && mkdir /data && chown 10001:10001 /data
COPY --chown=10001:10001 seatplan ./seatplan
COPY --chown=10001:10001 examples ./examples
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "seatplan.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-proxy-headers", "--limit-concurrency", "80"]
