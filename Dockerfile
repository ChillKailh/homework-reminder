FROM python:3.13-slim

WORKDIR /app
COPY main.py homework.json ./

CMD ["python", "main.py"]
