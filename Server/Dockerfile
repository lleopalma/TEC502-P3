FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install web3 py-solc-x
CMD ["python", "-u", "broker.py"]