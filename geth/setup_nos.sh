#!/usr/bin/env bash
# geth/setup_nos.sh
# Inicializa o banco de dados de cada nó Geth com o genesis e importa as chaves.
# Execute uma vez antes de subir o docker-compose.

set -e
BASE="$(cd "$(dirname "$0")" && pwd)"

echo "[setup] Inicializando nós Geth..."

for i in 1 2 3; do
  NODE_DIR="$BASE/node${i}"
  DATA_DIR="$NODE_DIR/data"
  
  # Arquivo de senha (vazio — chave não tem senha)
  echo "" > "$NODE_DIR/password.txt"

  # Importa a chave privada como keystore do Geth
  docker run --rm \
    -v "$BASE:/geth" \
    ethereum/client-go:v1.13.14 \
    account import \
      --datadir "/geth/node${i}/data" \
      --password "/geth/node${i}/password.txt" \
      "/geth/node${i}/account_key" 2>&1 | grep -v "^$" || true

  # Inicializa com o genesis
  docker run --rm \
    -v "$BASE:/geth" \
    ethereum/client-go:v1.13.14 \
    init \
      --datadir "/geth/node${i}/data" \
      "/geth/genesis.json" 2>&1 | tail -3

  echo "[setup] node${i} inicializado."
done

echo "[setup] Todos os nós prontos."
echo ""
echo "Agora execute:"
echo "  docker compose --profile geth up -d geth-node1 geth-node2 geth-node3"
echo "  sleep 10"
echo "  python3 geth/conectar_nos.py"