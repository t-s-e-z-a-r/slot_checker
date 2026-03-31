# Образ уже містить Python, Playwright і браузери (версію тега краще інколи оновлювати разом із локальним playwright).
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

RUN pip install --no-cache-dir "requests>=2.28.0,<3"

COPY main.py .

ENV PYTHONUNBUFFERED=1
ENV DOCKER=1

# Інтервал (сек): docker run ... python main.py 60
CMD ["python", "main.py", "30"]
