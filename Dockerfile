# Тег образу й версія playwright мають збігатися (браузери вже всередині образу).
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

# Явно ставимо playwright — на деяких збірках базовий образ без модуля в тому Python, що запускає CMD.
RUN pip install --no-cache-dir "requests>=2.28.0,<3" "playwright==1.58.0" \
    && python -c "from playwright.sync_api import sync_playwright"

COPY main.py .

ENV PYTHONUNBUFFERED=1
ENV DOCKER=1

# Інтервал (сек): docker run ... python main.py 60
CMD ["python", "main.py", "30"]
