# TEC502 — P3: Economia e Auditoria de Guerra — Estreito de Ormuz

Sistema distribuído para monitoramento marítimo com múltiplos brokers independentes,
exclusão mútua via Ricart-Agrawala, despacho prioritário de drones autônomos e
registro imutável de missões e créditos em blockchain Ethereum privada (Geth Clique PoA).

---

## Sumário

- [Visão geral](#visão-geral)
- [Arquitetura](#arquitetura)
- [Blockchain — Rede Geth](#blockchain--rede-geth)
- [Contratos inteligentes](#contratos-inteligentes)
- [Protocolo de comunicação](#protocolo-de-comunicação)
- [Exclusão mútua distribuída](#exclusão-mútua-distribuída)
- [Fila de requisições e priorização](#fila-de-requisições-e-priorização)
- [Tolerância a falhas](#tolerância-a-falhas)
- [Pré-requisitos](#pré-requisitos)
- [Estrutura de diretórios](#estrutura-de-diretórios)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Como executar — laboratório (4 máquinas)](#como-executar--laboratório-4-máquinas)
- [Como executar — máquina única](#como-executar--máquina-única)
- [Demonstração de tolerância a falha da blockchain](#demonstração-de-tolerância-a-falha-da-blockchain)
- [Testes](#testes)

---

## Visão geral

O sistema é composto por quatro tipos de componentes operacionais e uma camada de blockchain:

- **Broker** (`broker.py`, `tcp_server.py`, `udp_server.py`, `state.py`, `fila.py`): servidor de setor que recebe dados dos sensores, mantém a fila de requisições, coordena com outros brokers via exclusão mútua e despacha drones. Integra-se à blockchain para débito de créditos e registro de laudos.
- **Drone** (`drone.py`): atuador autônomo que se conecta via TCP ao broker do seu setor, recebe missões e reporta conclusão com heartbeat periódico.
- **Radar** (`sensor_radar.py`): sensor de risco de bloqueio de rota; envia percentual de obstrução via UDP.
- **Boia** (`sensor_boia.py`): sensor de detecção de embarcação à deriva; envia alerta binário via UDP.
- **Blockchain** (`Blockchain/blockchain.py`): integração Web3.py com rede Geth privada. Gerencia tokens de crédito (ORC) e registra laudos imutáveis de missões.

Não há servidor central. Cada broker opera de forma independente. A blockchain é uma rede Ethereum privada com 3 nós validadores rodando Clique PoA — qualquer nó pode cair e os outros dois continuam operando.

---

## Arquitetura

```
Máquina 1 — Broker A (172.16.201.2)
┌──────────────────────────────────────────┐
│  geth-node1 ──┐                          │
│  geth-node2 ──┼── Rede Ethereum privada  │
│  geth-node3 ──┘   (Clique PoA)          │
│                                          │
│  sensor_radar (UDP) ──►                  │
│  sensor_boia  (UDP) ──► broker-a ──────────────────► broker-b (172.16.201.4)
│                         (fila, RA,       │  TCP P2P   broker-c (172.16.201.8)
│  drone-a1 (TCP) ◄───── despacho,        │            broker-d (172.16.201.1)
│  drone-a2 (TCP) ◄───── blockchain)      │
└──────────────────────────────────────────┘

Máquinas 2, 3, 4 — Brokers B, C, D
┌─────────────────────────┐
│  sensor_radar (UDP) ──► │
│  sensor_boia  (UDP) ──► │ broker-x ◄──► blockchain (via IP da máquina 1)
│  drone-x1 (TCP) ◄────── │
│  drone-x2 (TCP) ◄────── │
└─────────────────────────┘
```

Os 3 nós Geth ficam na **máquina 1** e são acessíveis por todas as outras máquinas via IP. Cada broker se conecta aos 3 nós com fallback automático — se um nó cair, o broker usa o próximo disponível.

---

## Blockchain — Rede Geth

A rede blockchain utiliza **Go-Ethereum (Geth) v1.13.14** com consenso **Clique PoA** (Proof of Authority).

### Por que Clique PoA?

- Blocos produzidos a cada 5 segundos, sem mineração computacional.
- Finality quase instantânea — transações confirmadas rapidamente.
- Tolerância a falha: com 3 nós signatários, 1 pode cair e os outros 2 continuam produzindo blocos (maioria simples).
- Compatível com todas as ferramentas Ethereum padrão (Web3.py, MetaMask, etc.).

### Tolerância a falha da blockchain

Com 3 nós validadores, o sistema tolera a falha de 1 nó. Os outros 2 formam maioria e continuam validando transações e produzindo blocos. Ao voltar, o nó se sincroniza automaticamente com os demais.

### Arquivos gerados

```
geth/
├── genesis.json          ← configuração da rede (chainId 1337, Clique PoA)
├── accounts.json         ← endereços dos nós e da conta broker
├── broker_account_key    ← chave privada da conta que faz transações
├── node1/
│   ├── account_key       ← chave privada do validador 1
│   ├── password.txt      ← senha (vazia)
│   └── data/             ← banco de dados do nó (gerado pelo setup)
├── node2/ ...
└── node3/ ...
```

---

## Contratos inteligentes

### CreditToken.sol — Token ORC

Implementa o token de créditos operacionais da frota.

| Função | Descrição |
|---|---|
| `mint(endereço, quantidade)` | Emite créditos para uma empresa (apenas owner) |
| `transfer(para, valor)` | Transfere créditos entre empresas |
| `debitar(empresa, valor, req_id)` | Debita créditos ao despachar drone (apenas broker autorizado) |
| `saldo(endereço)` | Consulta saldo de uma empresa |

### MissionLog.sol — Log imutável

Registra laudos de missão de forma imutável na blockchain.

| Função | Descrição |
|---|---|
| `registrarLaudo(req_id, drone_id, setor, descricao, resultado)` | Registra laudo (apenas broker autorizado, sem duplicatas) |
| `obterLaudo(req_id)` | Consulta laudo por ID de requisição |
| `totalLaudos()` | Total de laudos registrados |
| `laudosPorSetor(setor)` | Lista todos os laudos de um setor |
| `listarLaudos(inicio, fim)` | Paginação do histórico completo |

---

## Protocolo de comunicação

Todas as mensagens são objetos JSON delimitados por `\n`. Codificação UTF-8.

### Mensagens sensor → broker (UDP)

**Radar** — envia a cada 5 segundos:
```json
{"tipo": "sensor", "dispositivo": "risco_bloqueio", "valor": 72, "unidade": "%", "zona": "setor-a"}
```

**Boia** — envia a cada 5 segundos:
```json
{"tipo": "sensor", "dispositivo": "boia", "valor": 1, "unidade": "bool", "boia_id": "boia-a1"}
```

### Mensagens drone → broker (TCP persistente)

```json
{"tipo": "identificacao", "dispositivo": "drone", "drone_id": "drone-a1"}
{"tipo": "heartbeat", "dispositivo": "drone", "drone_id": "drone-a1"}
{"tipo": "missao_concluida", "drone_id": "drone-a1", "req_id": "a-1716123456789"}
```

### Mensagens broker → drone (TCP)

```json
{"tipo": "confirmacao", "mensagem": "Registrado como DRONE no broker A"}
{"tipo": "comando", "acao": "INICIAR_MISSAO", "req_id": "a-1716123456789", "descricao": "Radar setor-a: risco 72%"}
```

### Mensagens broker ↔ broker (TCP — Ricart-Agrawala)

```json
{"tipo": "request_sc", "de": "a", "ts": 7}
{"tipo": "ok_sc", "de": "b", "ts": 8}
{"tipo": "encaminhar_req", "req": {...}, "de": "a"}
```

---

## Exclusão mútua distribuída

O sistema implementa **Ricart-Agrawala** com **Relógio de Lamport** para garantir que um mesmo drone nunca seja alocado por dois brokers simultaneamente.

### Protocolo

1. Broker quer alocar → incrementa relógio de Lamport e envia `REQUEST(ts, id)` para todos os peers.
2. Peer responde `OK` imediatamente se não está em WANTED/HELD, ou se está em WANTED mas o solicitante tem prioridade maior. Caso contrário, enfileira o `OK`.
3. Ao receber `OK` de todos (ou timeout) → entra na seção crítica, verifica créditos na blockchain e aloca o drone.
4. Ao sair → envia `OK` para todos que estavam na fila pendente.

### Estados

| Estado | Significado |
|---|---|
| `RELEASED` | Não está interessado na seção crítica |
| `WANTED` | Enviou REQUEST, aguardando OKs |
| `HELD` | Dentro da seção crítica, alocando drone |

---

## Fila de requisições e priorização

Cada broker mantém uma fila local ordenada por `(-criticidade, timestamp)`.

| Sensor | Condição | Criticidade |
|---|---|---|
| Boia | Deriva detectada | 5 |
| Radar | Risco ≥ 80% | 5 |
| Radar | Risco ≥ 60% | 4 |
| Radar | Risco ≥ 40% | 3 |
| Radar | Risco ≥ 20% | 2 |
| Radar | Risco < 20% | 1 |

Requisições com criticidade < 3 não disparam despacho. Quando um broker não tem drones disponíveis, encaminha as requisições mais críticas para os peers via `encaminhar_req`.

---

## Tolerância a falhas

### Falha de drone
O broker monitora heartbeats a cada 2s. Sem heartbeat por `DRONE_TIMEOUT` segundos → drone marcado como `FALHOU`, missão recolocada na fila e redistribuída.

### Falha de broker
Se um peer não responde ao `REQUEST` dentro de `OK_TIMEOUT * 2` segundos, o broker assume OK e avança. Os demais setores continuam operando normalmente.

### Falha de nó blockchain
O `blockchain.py` tenta os nós em ordem (`BESU_NODES`). Se o nó atual não responder, troca automaticamente para o próximo. Com 2 de 3 nós ativos, a rede continua produzindo blocos.

### Reconexão de drone
Drones possuem loop de reconexão automática com fallback para brokers alternativos.

---

## Pré-requisitos

- Docker 20.10 ou superior
- Docker Compose 2.0 ou superior
- Python 3.11 ou superior (para os scripts de setup)
- `pip install eth-account requests` (instalado automaticamente pelo `setup_geth.sh`)
- Rede com IPs acessíveis entre as 4 máquinas

---

## Estrutura de diretórios

```
TEC502-P3/
├── README.md
├── docker-compose.yml          # Compose com profiles por BROKER_ID
├── setup_geth.sh               # Setup completo da rede Geth (roda 1 vez)
├── .env                        # Variáveis de ambiente (um por máquina)
│
├── geth/                       # Rede blockchain Ethereum privada
│   ├── gerar_contas.py         # Gera chaves e genesis.json
│   ├── setup_nos.sh            # Inicializa os nós Geth
│   ├── conectar_nos.py         # Conecta peers após subir os containers
│   ├── genesis.json            # Configuração da rede (gerado)
│   ├── accounts.json           # Endereços dos nós e broker (gerado)
│   ├── broker_account_key      # Chave privada da conta broker (gerado)
│   ├── node1/ node2/ node3/    # Dados de cada nó validador
│   └── enodes.json             # Enodes dos nós (gerado pelo conectar_nos.py)
│
├── Server/
│   ├── broker.py               # Orquestrador: RA, alocação, monitoramento
│   ├── tcp_server.py           # Servidor TCP: drones e P2P entre brokers
│   ├── udp_server.py           # Servidor UDP: recepção de sensores
│   ├── state.py                # Estado global: Lamport, RA, fila, drones
│   ├── fila.py                 # Fila distribuída com encaminhamento
│   ├── Blockchain/
│   │   ├── blockchain.py       # Integração Web3.py com rede Geth
│   │   ├── CreditToken.sol     # Contrato do token ORC
│   │   └── MissionLog.sol      # Contrato de log imutável
│   └── Dockerfile
│
├── Actuators/
│   ├── drone.py                # Atuador drone: TCP, heartbeat, missão
│   └── Dockerfile
│
├── Sensors/
│   ├── sensor_radar.py         # Radar: risco de bloqueio (UDP)
│   ├── sensor_boia.py          # Boia: detecção de deriva (UDP)
│   └── Dockerfile
│
└── Tests/
    ├── teste.py                # Testes funcionais e de carga
    └── client.py               # Painel de consulta da blockchain
```

---

## Variáveis de ambiente

| Variável | Usado em | Descrição |
|---|---|---|
| `BROKER_ID` | broker | Identificador do setor: `a`, `b`, `c` ou `d` |
| `BROKER_PORT` | broker | Porta TCP do broker (padrão: `12345`) |
| `UDP_PORT` | broker | Porta UDP dos sensores (padrão: `12346`) |
| `PEERS` | broker | Peers: `"b:172.16.201.4:12345,c:172.16.201.8:12345"` |
| `DRONE_TIMEOUT` | broker | Segundos sem heartbeat para marcar drone como falhou (padrão: `10`) |
| `OK_TIMEOUT` | broker | Timeout para aguardar OK do Ricart-Agrawala (padrão: `5`) |
| `BESU_NODES` | broker | URLs dos nós Geth: `"http://172.16.201.2:8601,http://172.16.201.2:8602,http://172.16.201.2:8603"` |
| `BROKER_ACCOUNT_KEY` | broker | Chave privada hex da conta Ethereum do broker |
| `CONTRACT_ADDRESSES` | broker | JSON com endereços dos contratos já deployados (máquinas 2/3/4) |
| `BROKER_HOST` | sensores | Hostname ou IP do broker para envio UDP |
| `ZONA` | radar | Identificador de zona do radar |

---

## Como executar — laboratório (4 máquinas)

### IPs do laboratório

| Máquina | IP | Setor | Função extra |
|---|---|---|---|
| 1 | 172.16.201.2 | A | Roda os 3 nós Geth |
| 2 | 172.16.201.4 | B | Só broker |
| 3 | 172.16.201.8 | C | Só broker |
| 4 | 172.16.201.1 | D | Só broker |

### Passo 1 — Máquina 1: setup da blockchain (roda uma vez)

```bash
chmod +x setup_geth.sh
./setup_geth.sh
```

O script:
1. Cria arquivos de senha para os nós
2. Importa as chaves privadas no formato keystore do Geth
3. Inicializa o banco de dados de cada nó com o `genesis.json`
4. Sobe os 3 containers Geth
5. Conecta os peers entre si

Ao final, exibe a `BROKER_ACCOUNT_KEY` para copiar no `.env`.

### Passo 2 — Máquina 1: configure o `.env`

```bash
# .env — Máquina 1 (Broker A)
BROKER_ID=a
PEERS=b:172.16.201.4:12345,c:172.16.201.8:12345,d:172.16.201.1:12345
BROKER_ACCOUNT_KEY=<chave exibida pelo setup_geth.sh>
BESU_NODES=http://172.16.201.2:8601,http://172.16.201.2:8602,http://172.16.201.2:8603
```

### Passo 3 — Máquina 1: suba o broker A

```bash
docker compose --profile a up -d
```

Acompanhe o log até ver os contratos deployados:

```bash
docker logs -f broker-a | grep blockchain
# [blockchain] CreditToken → 0xAbCd...
# [blockchain] MissionLog  → 0x1234...
# [blockchain] Pronto.
```

Anote os endereços dos contratos — você vai precisar deles nas outras máquinas.

### Passo 4 — Máquinas 2, 3 e 4: configure o `.env`

Copie o projeto completo para cada máquina (incluindo a pasta `geth/`).

```bash
# .env — Máquina 2 (Broker B)
BROKER_ID=b
PEERS=a:172.16.201.2:12345,c:172.16.201.8:12345,d:172.16.201.1:12345
BROKER_ACCOUNT_KEY=<mesma chave da máquina 1>
BESU_NODES=http://172.16.201.2:8601,http://172.16.201.2:8602,http://172.16.201.2:8603
CONTRACT_ADDRESSES={"credit":"0xAbCd...","mission":"0x1234..."}
```

```bash
# .env — Máquina 3 (Broker C)
BROKER_ID=c
PEERS=a:172.16.201.2:12345,b:172.16.201.4:12345,d:172.16.201.1:12345
BROKER_ACCOUNT_KEY=<mesma chave da máquina 1>
BESU_NODES=http://172.16.201.2:8601,http://172.16.201.2:8602,http://172.16.201.2:8603
CONTRACT_ADDRESSES={"credit":"0xAbCd...","mission":"0x1234..."}
```

```bash
# .env — Máquina 4 (Broker D)
BROKER_ID=d
PEERS=a:172.16.201.2:12345,b:172.16.201.4:12345,c:172.16.201.8:12345
BROKER_ACCOUNT_KEY=<mesma chave da máquina 1>
BESU_NODES=http://172.16.201.2:8601,http://172.16.201.2:8602,http://172.16.201.2:8603
CONTRACT_ADDRESSES={"credit":"0xAbCd...","mission":"0x1234..."}
```

> **Por que `CONTRACT_ADDRESSES`?** O deploy dos contratos é feito apenas uma vez pelo primeiro broker que subir. Informando os endereços nas outras máquinas, elas reutilizam os contratos já existentes em vez de fazer um novo deploy.

### Passo 5 — Máquinas 2, 3 e 4: suba os brokers

```bash
# Máquina 2
docker compose --profile b up -d

# Máquina 3
docker compose --profile c up -d

# Máquina 4
docker compose --profile d up -d
```

### Verificar o sistema

```bash
# Logs do broker
docker logs -f broker-a

# Status dos nós Geth (máquina 1)
python3 geth/conectar_nos.py

# Painel blockchain
python3 Tests/client.py
```

### Parar tudo

```bash
# Cada máquina para seu próprio profile
docker compose --profile a down   # máquina 1
docker compose --profile b down   # máquina 2
# etc.
```

---

## Como executar — máquina única

Para testes locais com um único broker:

```bash
# .env
BROKER_ID=a
PEERS=
BROKER_ACCOUNT_KEY=<chave do broker_account_key>
BESU_NODES=http://geth-node1:8545,http://geth-node2:8545,http://geth-node3:8545

# Setup e execução
./setup_geth.sh
docker compose --profile a up -d
docker logs -f broker-a
```

---

## Demonstração de tolerância a falha da blockchain

Para mostrar que a rede continua operando com um nó derrubado:

```bash
# 1. Verifica estado inicial (3 nós, bloco avançando)
python3 geth/conectar_nos.py

# 2. Derruba o node2
docker stop geth-node2

# 3. Verifica que os outros 2 continuam produzindo blocos
sleep 15
python3 -c "
import requests
for port in [8601, 8603]:
    r = requests.post(f'http://localhost:{port}',
        json={'jsonrpc':'2.0','method':'eth_blockNumber','params':[],'id':1})
    print(f'porta {port}: bloco #{int(r.json()[\"result\"],16)}')
"

# 4. Verifica que os laudos continuam sendo registrados
docker logs broker-a | grep "Laudo registrado" | tail -5

# 5. Restaura o nó
docker start geth-node2
sleep 15
python3 geth/conectar_nos.py   # mostra sincronização
```

---

## Testes

O script `Tests/teste.py` cobre testes funcionais e de carga.

```bash
# Contra broker local
python Tests/teste.py

# Contra broker remoto
python Tests/teste.py --host 172.16.201.2 --port 12345 --udp-port 12346 --duracao 15
```

### Cenários cobertos

- Broker TCP acessível e handshake de drone correto
- Recepção de sensor UDP (radar e boia)
- Requisição de criticidade ≥ 4 enfileirada e despachada
- Conclusão de missão libera o drone e registra laudo na blockchain
- Tolerância a falha de drone (heartbeat + requeue)
- Teste de carga com múltiplos sensores e drones simultâneos
- Verificação de ausência de missões duplicadas