import socket
import json
import os
import time
import threading
import random

# Atuador: Drone
# Conecta via TCP ao broker do seu setor.
# Se o broker principal cair, tenta os brokers alternativos em sequência,
# permitindo que drones de um setor sejam absorvidos por outros brokers.
#
# Variáveis de ambiente:
#   SERVER_HOST  — broker principal (legado, usado se BROKERS não definido)
#   PORT         — porta do broker principal (legado)
#   BROKERS      — lista de brokers em ordem de preferência:
#                  "broker-a:12345,broker-b:12345,broker-c:12345"
#   DRONE_TIMEOUT — timeout de conexão por tentativa (padrão: 5s)

RETRY_INTERVAL    = 5
HEARTBEAT_INTERVAL = 2
DRONE_ID          = socket.gethostname()  # ID único via nome do container
CONNECT_TIMEOUT   = float(os.environ.get("DRONE_TIMEOUT", "5"))


def _parse_brokers() -> list:
    """
    Monta lista de brokers a partir de PEERS (variável já existente no broker).
    Coloca o broker local primeiro — prioridade máxima.
    Se PEERS não estiver definido, retorna apenas o broker local.
    Fallback para SERVER_HOST:PORT se BROKER_ID também não estiver definido.

    Formato de PEERS: "B:172.16.201.4:12345,C:172.16.201.8:12345"
    """
    broker_id = os.environ.get("BROKER_ID", "").strip().lower()
    port      = int(os.environ.get("PORT", "12345"))

    if broker_id:
        local = (f"broker-{broker_id}", port)
    else:
        # Fallback legado
        local = (os.environ.get("SERVER_HOST", "broker-a"), port)

    brokers = [local]

    peers_raw = os.environ.get("PEERS", "").strip()
    for entry in peers_raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        partes = entry.split(":")
        if len(partes) == 3:
            _, host, peer_port = partes   # descarta o ID (ex: "B")
            brokers.append((host, int(peer_port)))

    return brokers


BROKERS = _parse_brokers()


def enviar(s, **campos):
    """Envia uma mensagem JSON ao Broker."""
    mensagem = json.dumps(campos, ensure_ascii=False) + "\n"
    s.sendall(mensagem.encode("utf-8"))


def ler_linha(s, buffer):
    """Lê do socket até ter uma linha completa, retorna (linha, buffer_restante)."""
    while "\n" not in buffer:
        chunk = s.recv(1024).decode("utf-8")
        if not chunk:
            raise ConnectionError("Conexão encerrada pelo servidor.")
        buffer += chunk
    linha, buffer = buffer.split("\n", 1)
    return linha.strip(), buffer


def conectar() -> tuple:
    """
    Tenta conectar aos brokers em ordem de preferência.
    Cicla pela lista indefinidamente até obter conexão.
    Retorna (socket, host, port) do broker conectado.
    """
    idx = 0
    while True:
        host, port = BROKERS[idx % len(BROKERS)]
        try:
            print(f"[{DRONE_ID}] Tentando {host}:{port}...")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            s.connect((host, port))
            s.settimeout(None)
            print(f"[{DRONE_ID}] Conectado a {host}:{port}\n")
            return s, host, port
        except Exception as e:
            print(f"[{DRONE_ID}] Falha em {host}:{port}: {e}")
            idx += 1
            # Só espera RETRY_INTERVAL após tentar todos os brokers
            if idx % len(BROKERS) == 0:
                print(f"[{DRONE_ID}] Todos os brokers indisponíveis. Aguardando {RETRY_INTERVAL}s...")
                time.sleep(RETRY_INTERVAL)


def enviar_heartbeat(s, stop_event):
    """Envia heartbeat a cada 2 segundos até o evento ser definido."""
    while not stop_event.is_set():
        try:
            enviar(s, tipo="heartbeat", dispositivo="drone", drone_id=DRONE_ID)
        except Exception as e:
            print(f"[{DRONE_ID}] Erro ao enviar heartbeat: {e}")
            break
        time.sleep(HEARTBEAT_INTERVAL)


def executar_missao(sock, req_id):
    """Simula a execução de uma missão e envia confirmação de conclusão."""
    duracao = random.randint(2, 5)  # segundos
    print(f"[{DRONE_ID}] Missão {req_id} iniciada. Duração estimada: {duracao}s")
    time.sleep(duracao)
    try:
        enviar(sock, tipo="missao_concluida", drone_id=DRONE_ID, req_id=req_id)
        print(f"[{DRONE_ID}] Missão {req_id} concluída.")
    except Exception as e:
        print(f"[{DRONE_ID}] Erro ao confirmar missão {req_id}: {e}")


# ── Loop principal ────────────────────────────────────────────

while True:
    s, broker_host, broker_port = conectar()
    stop_event = threading.Event()

    try:
        # Identificação
        enviar(s, tipo="identificacao", dispositivo="drone", drone_id=DRONE_ID)

        # Confirmação do broker
        buffer = ""
        linha, buffer = ler_linha(s, buffer)
        confirmacao = json.loads(linha)
        assert confirmacao.get("tipo") == "confirmacao", f"Esperado confirmacao, recebido: {confirmacao}"
        print(f"[{DRONE_ID}] {confirmacao.get('mensagem')}")
        print(f"[{DRONE_ID}] Aguardando comandos...\n")

        # Inicia thread de heartbeat
        heartbeat_thread = threading.Thread(
            target=enviar_heartbeat, args=(s, stop_event), daemon=True
        )
        heartbeat_thread.start()

        # Loop de recebimento de comandos
        while True:
            chunk = s.recv(1024).decode("utf-8")
            if not chunk:
                print(f"[{DRONE_ID}] Conexão encerrada por {broker_host}.")
                break

            buffer += chunk
            while "\n" in buffer:
                linha, buffer = buffer.split("\n", 1)
                linha = linha.strip()
                if not linha:
                    continue

                dados = json.loads(linha)

                if dados.get("tipo") != "comando":
                    continue

                acao = dados.get("acao", "")

                if acao == "INICIAR_MISSAO":
                    print(f"[{DRONE_ID}] Comando recebido: {acao}")
                    threading.Thread(
                        target=executar_missao,
                        args=(s, dados.get("req_id")),
                        daemon=True
                    ).start()
                else:
                    print(f"[{DRONE_ID}] Comando desconhecido: {acao}")

    except Exception as e:
        print(f"[{DRONE_ID}] Erro na conexão com {broker_host}: {e}")
    finally:
        stop_event.set()
        s.close()

    print(f"[{DRONE_ID}] Desconectado de {broker_host}. Buscando broker alternativo...\n")