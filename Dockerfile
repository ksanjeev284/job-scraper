FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -e ".[web,browser]" \
    && python -m playwright install --with-deps chromium

EXPOSE 8000

# Binds to all interfaces for container use; put it behind a reverse proxy
# with auth if you expose it beyond localhost.
CMD ["jobscraper-serve", "--host", "0.0.0.0", "--port", "8000"]
