FROM mcr.microsoft.com/playwright/python:v1.65.0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start.sh

ENV FLASK_APP=app:create_app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000 9222

CMD ["./start.sh"]
