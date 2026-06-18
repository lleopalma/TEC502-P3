#!/usr/bin/env bash
# setup_geth.sh — Setup completo da rede Geth para o projeto Ormuz
# Execute uma vez antes de subir o sistema.
#
# Uso:
#   chmod +x setup_geth.sh && ./setup_geth.sh

set -e

AMARELO='\033[1;33m'
VERDE='\033[0;32m'
NC='\033[0m'

info() { echo -e "${AMARELO}[setup]${NC} $*"; }
ok()   { echo -e "${VERDE}[ok]${NC} $*"; }

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
GETH_DIR="$BASE_DIR/geth"

# ── 1. Cria arquivos de senha (vazios) ───────────────────────
info "Criando arquivos de senha..."
for i in 1 2 3; do
  echo "" > "$GETH_DIR/node${i}/password.txt"
done
ok "Senhas criadas."

# ── 2. Importa chaves e inicializa cada nó ───────────────────
info "Inicializando nós Geth (importando chaves e genesis)..."
for i in 1 2 3; do
  info "  Importando chave do node${i}..."
  docker run --rm \
    -v "$GETH_DIR:/geth" \
    ethereum/client-go:v1.13.14 \
    account import \
      --datadir "/geth/node${i}/data" \
      --password "/geth/node${i}/password.txt" \
      "/geth/node${i}/account_key" 2>&1 | grep -E "Address|already" || true

  info "  Inicializando genesis no node${i}..."
  docker run --rm \
    -v "$GETH_DIR:/geth" \
    ethereum/client-go:v1.13.14 \
    init \
      --datadir "/geth/node${i}/data" \
      "/geth/genesis.json" 2>&1 | grep -E "committed|already" || true

  ok "  node${i} pronto."
done

# ── 3. Exibe chave do broker ─────────────────────────────────
BROKER_KEY=$(cat "$GETH_DIR/broker_account_key")
BROKER_ADDR=$(cat "$GETH_DIR/broker_address")

echo ""
info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Adicione ao seu .env:"
echo ""
echo "  BROKER_ACCOUNT_KEY=${BROKER_KEY}"
echo ""
info "Endereço da conta broker: ${BROKER_ADDR}"
info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 4. Sobe os nós ───────────────────────────────────────────
info "Subindo nós Geth..."
docker compose --profile geth up -d geth-node1 geth-node2 geth-node3
ok "Containers Geth iniciados."

# ── 5. Conecta peers ─────────────────────────────────────────
info "Aguardando nós iniciarem (15s)..."
sleep 15
python3 geth/conectar_nos.py && ok "Peers conectados." || \
  echo -e "${AMARELO}[aviso]${NC} Execute manualmente: python3 geth/conectar_nos.py"

echo ""
ok "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "Setup concluído! Próximos passos:"
echo ""
echo "  1. Adicione BROKER_ACCOUNT_KEY ao .env (mostrado acima)"
echo "  2. Suba o broker: docker compose --profile a up -d"
echo "  3. Copie CONTRACT_ADDRESSES do log para o .env das outras máquinas"
ok "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"