"""
blockchain.py — Integração Web3 com rede Besu (Clique PoA)
===========================================================
Conecta a uma rede Besu com múltiplos nós. Se um nó cair, reconecta
automaticamente ao próximo da lista (tolerância a falha).

Compatível com o Ganache original: basta apontar BESU_NODES para um
único nó Ganache e funciona igual ao código anterior.

Variáveis de ambiente:
    BESU_NODES           — URLs dos nós separadas por vírgula
                           ex: "http://besu-node1:8545,http://besu-node2:8545,http://besu-node3:8545"
                           fallback: GANACHE_URL (retrocompatibilidade)
    BROKER_ACCOUNT_KEY   — chave privada hex da conta do broker
                           (gerada por gerar_genesis.py, arquivo besu/broker_account_key)
    CREDIT_COST          — custo em créditos por requisição (padrão: 1)
    CONTRACT_ADDRESSES   — JSON com endereços já deployados
                           ex: '{"credit":"0x...","mission":"0x..."}'
"""

import json
import os
import threading
import time
from pathlib import Path

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from solcx import compile_source, install_solc

# ── Configuração ────────────────────────────────────────────

def _carregar_nos() -> list:
    """Retorna lista de URLs de nós Besu, com fallback para GANACHE_URL."""
    besu = os.environ.get("BESU_NODES", "").strip()
    if besu:
        return [u.strip() for u in besu.split(",") if u.strip()]
    # retrocompatibilidade com variável antiga
    ganache = os.environ.get("GANACHE_URL", "http://127.0.0.1:8545").strip()
    return [ganache]

def _carregar_chave_broker() -> str | None:
    """
    Carrega a chave privada do broker.
    Primeiro tenta BROKER_ACCOUNT_KEY como valor direto,
    depois como caminho de arquivo.
    """
    val = os.environ.get("BROKER_ACCOUNT_KEY", "").strip()
    if val:
        # pode ser a chave diretamente (hex) ou caminho de arquivo
        if val.startswith("0x") or len(val) == 64:
            return val
        try:
            return Path(val).read_text().strip()
        except Exception:
            pass

    # tenta arquivo padrão gerado pelo gerar_genesis.py
    default = Path(__file__).parent / "broker_account_key"
    if default.exists():
        return default.read_text().strip()

    return None  # usará accounts[0] como fallback (compatível com Ganache)


NOS_BESU     = _carregar_nos()
CREDIT_COST  = int(os.environ.get("CREDIT_COST", "1"))
SOLC_VERSION = "0.8.20"

_ADDRESSES_FILE = Path(__file__).parent / "contract_addresses.json"

# ── Contratos Solidity (inline) ──────────────────────────────

CREDIT_TOKEN_SOURCE = r"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract CreditToken {
    string  public name     = "OrmuzCredit";
    string  public symbol   = "ORC";
    address public owner;
    uint256 public totalSupply;

    mapping(address => uint256) private _balances;
    mapping(address => bool)    public  brokerAutorizado;

    event Transferencia(address indexed de, address indexed para, uint256 valor);
    event Emissao(address indexed para, uint256 valor);
    event Debito(address indexed empresa, uint256 valor, string req_id);

    modifier apenasOwner()  { require(msg.sender == owner,              "Apenas owner");  _; }
    modifier apenasBroker() { require(brokerAutorizado[msg.sender],     "Apenas broker"); _; }

    constructor() { owner = msg.sender; }

    function autorizarBroker(address broker) external apenasOwner {
        brokerAutorizado[broker] = true;
    }

    function mint(address para, uint256 quantidade) external apenasOwner {
        require(para != address(0) && quantidade > 0, "Parametros invalidos");
        _balances[para] += quantidade;
        totalSupply     += quantidade;
        emit Emissao(para, quantidade);
    }

    function transfer(address para, uint256 valor) external returns (bool) {
        require(_balances[msg.sender] >= valor, "Saldo insuficiente");
        _balances[msg.sender] -= valor;
        _balances[para]       += valor;
        emit Transferencia(msg.sender, para, valor);
        return true;
    }

    function debitar(address empresa, uint256 valor, string calldata req_id)
        external apenasBroker returns (bool)
    {
        require(_balances[empresa] >= valor, "Creditos insuficientes");
        _balances[empresa] -= valor;
        emit Debito(empresa, valor, req_id);
        return true;
    }

    function saldo(address endereco) external view returns (uint256) {
        return _balances[endereco];
    }

    function balanceOf(address endereco) external view returns (uint256) {
        return _balances[endereco];
    }
}
"""

MISSION_LOG_SOURCE = r"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract MissionLog {
    address public owner;
    mapping(address => bool) public brokerAutorizado;

    struct Laudo {
        string  req_id;
        string  drone_id;
        string  setor;
        string  descricao;
        string  resultado;
        uint256 timestamp;
        address broker;
        bool    existe;
    }

    mapping(string => Laudo)    private _laudos;
    mapping(string => string[]) private _laudosPorSetor;
    string[]                    private _todosReqIds;

    event LaudoRegistrado(
        string indexed req_id,
        string drone_id,
        string setor,
        string resultado,
        uint256 timestamp
    );

    modifier apenasOwner()  { require(msg.sender == owner,          "Apenas owner");  _; }
    modifier apenasBroker() { require(brokerAutorizado[msg.sender], "Apenas broker"); _; }

    constructor() { owner = msg.sender; }

    function autorizarBroker(address broker) external apenasOwner {
        brokerAutorizado[broker] = true;
    }

    function registrarLaudo(
        string calldata req_id,
        string calldata drone_id,
        string calldata setor,
        string calldata descricao,
        string calldata resultado
    ) external apenasBroker {
        require(bytes(req_id).length > 0, "req_id invalido");
        require(!_laudos[req_id].existe,  "Laudo ja registrado");

        _laudos[req_id] = Laudo(req_id, drone_id, setor, descricao,
                                resultado, block.timestamp, msg.sender, true);
        _laudosPorSetor[setor].push(req_id);
        _todosReqIds.push(req_id);

        emit LaudoRegistrado(req_id, drone_id, setor, resultado, block.timestamp);
    }

    function obterLaudo(string calldata req_id) external view returns (
        string memory drone_id, string memory setor,
        string memory descricao, string memory resultado,
        uint256 timestamp, address broker
    ) {
        require(_laudos[req_id].existe, "Laudo nao encontrado");
        Laudo storage l = _laudos[req_id];
        return (l.drone_id, l.setor, l.descricao, l.resultado, l.timestamp, l.broker);
    }

    function totalLaudos() external view returns (uint256) {
        return _todosReqIds.length;
    }

    function laudosPorSetor(string calldata setor) external view returns (string[] memory) {
        return _laudosPorSetor[setor];
    }

    function listarLaudos(uint256 inicio, uint256 fim)
        external view returns (string[] memory)
    {
        require(fim <= _todosReqIds.length && inicio < fim, "Intervalo invalido");
        string[] memory res = new string[](fim - inicio);
        for (uint256 i = inicio; i < fim; i++) res[i - inicio] = _todosReqIds[i];
        return res;
    }
}
"""

# ── Estado global ────────────────────────────────────────────

w3               = None
broker_account   = None
broker_priv_key  = None   # usada para assinar txs na Besu (não há conta desbloqueada)
credit_contract  = None
mission_contract = None
_inicializado    = False
_lock            = threading.Lock()

_no_atual_idx    = 0      # índice do nó ativo em NOS_BESU
_tx_lock         = threading.Lock()  # garante nonce sequencial entre threads


# ── Conexão com fallback ─────────────────────────────────────

def _conectar_no(url: str) -> Web3 | None:
    """Tenta conectar a um nó. Retorna instância Web3 ou None."""
    try:
        instance = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 5}))
        instance.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if instance.is_connected():
            return instance
    except Exception:
        pass
    return None


def _obter_w3() -> Web3:
    """
    Retorna uma conexão ativa. Se o nó atual falhou, tenta os demais em ordem.
    Lança RuntimeError se nenhum nó responder.
    """
    global w3, _no_atual_idx

    # Testa nó atual primeiro
    if w3 is not None:
        try:
            w3.eth.block_number  # ping
            return w3
        except Exception:
            print(f"[blockchain] Nó {NOS_BESU[_no_atual_idx]} caiu. Procurando alternativo...")

    # Tenta cada nó em ordem
    for i, url in enumerate(NOS_BESU):
        conn = _conectar_no(url)
        if conn:
            _no_atual_idx = i
            w3 = conn
            print(f"[blockchain] Conectado ao nó {i+1}: {url}")
            return w3

    raise RuntimeError(f"[blockchain] Nenhum nó Besu disponível. URLs: {NOS_BESU}")


# ── Transações ───────────────────────────────────────────────

def _tx(func, gas: int = 300_000):
    """
    Envia transação. Suporta dois modos:
    - Besu/rede real: assina localmente com chave privada (broker_priv_key)
    - Ganache dev: usa transact() com conta desbloqueada (retrocompatibilidade)

    O _tx_lock serializa chamadas concorrentes de threads diferentes,
    evitando que dois laudos busquem o mesmo nonce simultaneamente
    ('replacement transaction underpriced').
    """
    with _tx_lock:
        conn = _obter_w3()

        if broker_priv_key:
            # Modo Besu: assina e envia raw transaction
            # "pending" inclui txs ainda no mempool -> nonce sempre incrementado
            nonce = conn.eth.get_transaction_count(broker_account, "pending")
            tx = func.build_transaction({
                "from":     broker_account,
                "gas":      gas,
                "nonce":    nonce,
                "chainId":  conn.eth.chain_id,
                "gasPrice": conn.eth.gas_price,
            })
            signed = conn.eth.account.sign_transaction(tx, broker_priv_key)
            tx_hash = conn.eth.send_raw_transaction(signed.raw_transaction)
        else:
            # Modo Ganache: conta desbloqueada
            tx_hash = func.transact({"from": broker_account, "gas": gas})

        return conn.eth.wait_for_transaction_receipt(tx_hash, timeout=60)


# ── Compilação e deploy ──────────────────────────────────────

def _compilar_abi(nome: str, source: str) -> list:
    install_solc(SOLC_VERSION)
    compilado = compile_source(source, output_values=["abi", "bin"],
                               solc_version=SOLC_VERSION)
    chave = next(k for k in compilado if nome in k)
    return compilado[chave]["abi"]


def _deploy(nome: str, source: str):
    install_solc(SOLC_VERSION)
    compilado = compile_source(source, output_values=["abi", "bin"],
                               solc_version=SOLC_VERSION)
    chave    = next(k for k in compilado if nome in k)
    abi      = compilado[chave]["abi"]
    bytecode = compilado[chave]["bin"]

    conn     = _obter_w3()
    contrato = conn.eth.contract(abi=abi, bytecode=bytecode)

    if broker_priv_key:
        nonce = conn.eth.get_transaction_count(broker_account, "pending")
        tx = contrato.constructor().build_transaction({
            "from":     broker_account,
            "gas":      3_000_000,
            "nonce":    nonce,
            "chainId":  conn.eth.chain_id,
            "gasPrice": conn.eth.gas_price,
        })
        signed  = conn.eth.account.sign_transaction(tx, broker_priv_key)
        tx_hash = conn.eth.send_raw_transaction(signed.raw_transaction)
    else:
        tx_hash = contrato.constructor().transact({
            "from": broker_account, "gas": 3_000_000
        })

    receipt = conn.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return conn.eth.contract(address=receipt.contractAddress, abi=abi)


def _salvar_enderecos(credit_addr: str, mission_addr: str):
    with open(_ADDRESSES_FILE, "w") as f:
        json.dump({"credit": credit_addr, "mission": mission_addr}, f)


def _carregar_enderecos() -> dict | None:
    env = os.environ.get("CONTRACT_ADDRESSES", "").strip()
    if env:
        try:
            return json.loads(env)
        except Exception:
            pass
    if _ADDRESSES_FILE.exists():
        with open(_ADDRESSES_FILE) as f:
            return json.load(f)
    return None


# ── Inicialização ────────────────────────────────────────────

def inicializar():
    """
    Conecta à rede Besu, faz deploy dos contratos se necessário
    e autoriza a conta do broker.
    """
    global w3, broker_account, broker_priv_key
    global credit_contract, mission_contract, _inicializado

    with _lock:
        if _inicializado:
            return

        print(f"[blockchain] Conectando à rede Besu... ({len(NOS_BESU)} nó(s))")
        conn = _obter_w3()
        print(f"[blockchain] Conectado. Bloco atual: {conn.eth.block_number}")
        print(f"[blockchain] Chain ID: {conn.eth.chain_id}")
        print(f"[blockchain] Peers: {conn.net.peer_count}")

        # Conta do broker
        chave = _carregar_chave_broker()
        if chave:
            from eth_account import Account as EthAccount
            acct = EthAccount.from_key(chave)
            broker_priv_key = chave
            broker_account  = acct.address
            print(f"[blockchain] Conta broker (chave própria): {broker_account}")
        else:
            # Fallback Ganache: usa primeira conta desbloqueada
            broker_account  = conn.eth.accounts[0]
            broker_priv_key = None
            print(f"[blockchain] Conta broker (desbloqueada): {broker_account}")

        # Contratos
        saved = _carregar_enderecos()
        if saved:
            print(f"[blockchain] Reutilizando contratos: {saved}")
            credit_abi  = _compilar_abi("CreditToken", CREDIT_TOKEN_SOURCE)
            mission_abi = _compilar_abi("MissionLog",  MISSION_LOG_SOURCE)
            credit_contract  = conn.eth.contract(address=saved["credit"],  abi=credit_abi)
            mission_contract = conn.eth.contract(address=saved["mission"], abi=mission_abi)
        else:
            print("[blockchain] Fazendo deploy dos contratos...")
            install_solc(SOLC_VERSION)
            credit_contract  = _deploy("CreditToken", CREDIT_TOKEN_SOURCE)
            mission_contract = _deploy("MissionLog",  MISSION_LOG_SOURCE)
            _salvar_enderecos(credit_contract.address, mission_contract.address)
            print(f"[blockchain] CreditToken → {credit_contract.address}")
            print(f"[blockchain] MissionLog  → {mission_contract.address}")

            # Autoriza broker nos contratos
            _tx(credit_contract.functions.autorizarBroker(broker_account))
            _tx(mission_contract.functions.autorizarBroker(broker_account))
            print("[blockchain] Broker autorizado nos contratos.")

        _inicializado = True
        print(f"[blockchain] Pronto. Nós disponíveis: {NOS_BESU}\n")


# ── API pública (idêntica ao código anterior) ────────────────

def verificar_e_debitar(empresa: str, req_id: str) -> tuple:
    """Verifica saldo e debita créditos. Retorna (True, None) ou (False, motivo)."""
    _garantir_inicializado()
    try:
        conn         = _obter_w3()
        empresa_addr = Web3.to_checksum_address(empresa)
        # Usa o contrato apontado para o nó ativo
        ct = conn.eth.contract(
            address=credit_contract.address,
            abi=credit_contract.abi
        )
        saldo_atual = ct.functions.saldo(empresa_addr).call()

        if saldo_atual < CREDIT_COST:
            return False, f"Créditos insuficientes ({saldo_atual}/{CREDIT_COST})"

        _tx(ct.functions.debitar(empresa_addr, CREDIT_COST, req_id))
        print(f"[blockchain] Debitado {CREDIT_COST} ORC de {empresa_addr} (req {req_id})")
        return True, None

    except Exception as e:
        return False, str(e)


def registrar_laudo(req_id: str, drone_id: str, setor: str,
                    descricao: str, resultado: str) -> bool:
    """Registra laudo imutável na blockchain."""
    _garantir_inicializado()
    try:
        conn = _obter_w3()
        ml = conn.eth.contract(
            address=mission_contract.address,
            abi=mission_contract.abi
        )
        _tx(ml.functions.registrarLaudo(req_id, drone_id, setor, descricao, resultado))
        print(f"[blockchain] Laudo registrado on-chain: {req_id}")
        return True
    except Exception as e:
        print(f"[blockchain] Erro ao registrar laudo {req_id}: {e}")
        return False


def emitir_creditos(empresa: str, quantidade: int) -> bool:
    """Emite créditos ORC para uma empresa."""
    _garantir_inicializado()
    try:
        conn         = _obter_w3()
        empresa_addr = Web3.to_checksum_address(empresa)
        ct = conn.eth.contract(address=credit_contract.address, abi=credit_contract.abi)
        _tx(ct.functions.mint(empresa_addr, quantidade))
        print(f"[blockchain] Emitidos {quantidade} ORC para {empresa_addr}")
        return True
    except Exception as e:
        print(f"[blockchain] Erro ao emitir créditos: {e}")
        return False


def consultar_saldo(empresa: str) -> int:
    """Retorna saldo de créditos de uma empresa."""
    _garantir_inicializado()
    try:
        conn = _obter_w3()
        ct = conn.eth.contract(address=credit_contract.address, abi=credit_contract.abi)
        return ct.functions.saldo(Web3.to_checksum_address(empresa)).call()
    except Exception:
        return -1


def obter_laudo(req_id: str) -> dict | None:
    """Retorna laudo de uma missão pelo req_id."""
    _garantir_inicializado()
    try:
        conn = _obter_w3()
        ml = conn.eth.contract(address=mission_contract.address, abi=mission_contract.abi)
        drone_id, setor, descricao, resultado, timestamp, broker = \
            ml.functions.obterLaudo(req_id).call()
        return {
            "req_id":    req_id,
            "drone_id":  drone_id,
            "setor":     setor,
            "descricao": descricao,
            "resultado": resultado,
            "timestamp": timestamp,
            "broker":    broker,
        }
    except Exception:
        return None


def total_laudos() -> int:
    _garantir_inicializado()
    try:
        conn = _obter_w3()
        ml = conn.eth.contract(address=mission_contract.address, abi=mission_contract.abi)
        return ml.functions.totalLaudos().call()
    except Exception:
        return 0


def listar_laudos_recentes(n: int = 10) -> list:
    _garantir_inicializado()
    try:
        conn  = _obter_w3()
        ml    = conn.eth.contract(address=mission_contract.address, abi=mission_contract.abi)
        total = ml.functions.totalLaudos().call()
        if total == 0:
            return []
        inicio  = max(0, total - n)
        req_ids = ml.functions.listarLaudos(inicio, total).call()
        laudos  = [obter_laudo(rid) for rid in req_ids]
        return list(reversed([l for l in laudos if l]))
    except Exception as e:
        print(f"[blockchain] Erro ao listar laudos: {e}")
        return []


def laudos_por_setor(setor: str) -> list:
    _garantir_inicializado()
    try:
        conn    = _obter_w3()
        ml      = conn.eth.contract(address=mission_contract.address, abi=mission_contract.abi)
        req_ids = ml.functions.laudosPorSetor(setor).call()
        return [obter_laudo(rid) for rid in req_ids if obter_laudo(rid)]
    except Exception:
        return []


def endereco_contratos() -> dict:
    _garantir_inicializado()
    return {
        "credit":  credit_contract.address,
        "mission": mission_contract.address,
    }


def status_rede() -> dict:
    """Retorna status dos nós da rede Besu (útil para demonstração)."""
    resultado = {}
    for url in NOS_BESU:
        conn = _conectar_no(url)
        if conn:
            try:
                resultado[url] = {
                    "online":  True,
                    "bloco":   conn.eth.block_number,
                    "peers":   conn.net.peer_count,
                    "chainId": conn.eth.chain_id,
                }
            except Exception as e:
                resultado[url] = {"online": False, "erro": str(e)}
        else:
            resultado[url] = {"online": False}
    return resultado


def _garantir_inicializado():
    if not _inicializado:
        raise RuntimeError("blockchain.inicializar() não foi chamado")


def inicializar_com_retry(tentativas: int = 10, intervalo: int = 10):
    """
    Tenta inicializar em background, com retry.
    Chame essa função em vez de inicializar() no broker.py.
    """
    import threading

    def _tentar():
        global _inicializado
        for i in range(tentativas):
            try:
                inicializar()
                return
            except Exception as e:
                print(f"[blockchain] Tentativa {i+1}/{tentativas} falhou: {e}")
                if i < tentativas - 1:
                    print(f"[blockchain] Aguardando {intervalo}s...")
                    time.sleep(intervalo)
        print(f"[blockchain] Não foi possível conectar após {tentativas} tentativas.")

    t = threading.Thread(target=_tentar, daemon=True, name="blockchain-init")
    t.start()