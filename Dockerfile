FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY newgreedy.py newgreedy_addon.py config.ini ./
EXPOSE 3456
CMD ["python", "newgreedy.py"]
