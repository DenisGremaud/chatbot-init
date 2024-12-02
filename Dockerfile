FROM python:3.12.0

WORKDIR /app

COPY requirements.txt .

RUN pip install --upgrade pip

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
# COPY configs /app/configs

COPY data /app/data

CMD ["python", "main.py"]