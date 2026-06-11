"""
blockchain.py — Integração Web3 com Ganache
============================================
Módulo responsável por toda a comunicação com a blockchain local.
Conecta ao Ganache, compila e faz deploy dos contratos na primeira
execução, e expõe funções de alto nível para o broker e o cliente.

Dependências:
    pip install web3 py-solc-x

Ganache deve estar rodando em:
    http://127.0.0.1:7545  (Ganache GUI)
    ou
    http://127.0.0.1:8545  (ganache-cli / npx ganache)

Variáveis de ambiente (opcionais):
    GANACHE_URL          — URL do nó Ganache (padrão: http://127.0.0.1:8545)
    BROKER_ACCOUNT_IDX   — índice da conta Ganache usada pelo broker (padrão: 0)
    CREDIT_COST          — custo em créditos por requisição de drone (padrão: 1)
    CONTRACT_ADDRESSES   — JSON com endereços já deployados (evita redeploy)
                           ex: '{"credit":"0x...","mission":"0x..."}'
"""

import json
import os
import time
from pathlib import Path

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from solcx import compile_source, install_solc

# Configuração

GANACHE_URL        = os.environ.get("GANACHE_URL", "http://127.0.0.1:8545")
BROKER_ACCOUNT_IDX = int(os.environ.get("BROKER_ACCOUNT_IDX", "0"))
CREDIT_COST        = int(os.environ.get("CREDIT_COST", "1"))
SOLC_VERSION       = "0.8.20"

# Caminho para salvar endereços dos contratos deployados
_ADDRESSES_FILE = Path(__file__).parent / "contract_addresses.json"

# ── Código-fonte dos contratos (inline para portabilidade) ────

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

    modifier apenasOwner()  { require(msg.sender == owner,                  "Apenas owner");   _; }
    modifier apenasBroker() { require(brokerAutorizado[msg.sender],         "Apenas broker");  _; }

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

    modifier apenasOwner()  { require(msg.sender == owner,             "Apenas owner");  _; }
    modifier apenasBroker() { require(brokerAutorizado[msg.sender],    "Apenas broker"); _; }

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
        require(bytes(req_id).length > 0,  "req_id invalido");
        require(!_laudos[req_id].existe,   "Laudo ja registrado");

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

# Estado global do módulo

w3              = None
broker_account  = None
credit_contract = None
mission_contract = None
_inicializado   = False


# Inicialização

def inicializar():
    """
    Conecta ao Ganache, instala o compilador Solidity se necessário,
    faz deploy dos contratos (ou reutiliza endereços existentes)
    e autoriza a conta do broker nos dois contratos.
    Deve ser chamado uma vez na inicialização do broker.
    """
    global w3, broker_account, credit_contract, mission_contract, _inicializado

    if _inicializado:
        return

    print("[blockchain] Conectando ao Ganache...")
    w3 = Web3(Web3.HTTPProvider(GANACHE_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    if not w3.is_connected():
        raise ConnectionError(f"[blockchain] Não foi possível conectar ao Ganache em {GANACHE_URL}")

    print(f"[blockchain] Conectado. Bloco atual: {w3.eth.block_number}")

    broker_account = w3.eth.accounts[BROKER_ACCOUNT_IDX]
    print(f"[blockchain] Conta do broker: {broker_account}")

    # Verifica se contratos já foram deployados
    saved = _carregar_enderecos()
    if saved:
        print(f"[blockchain] Reutilizando contratos existentes: {saved}")
        credit_abi  = _compilar_abi("CreditToken", CREDIT_TOKEN_SOURCE)
        mission_abi = _compilar_abi("MissionLog",  MISSION_LOG_SOURCE)
        credit_contract  = w3.eth.contract(address=saved["credit"],  abi=credit_abi)
        mission_contract = w3.eth.contract(address=saved["mission"], abi=mission_abi)
    else:
        print("[blockchain] Fazendo deploy dos contratos...")
        install_solc(SOLC_VERSION)
        credit_contract  = _deploy("CreditToken", CREDIT_TOKEN_SOURCE)
        mission_contract = _deploy("MissionLog",  MISSION_LOG_SOURCE)
        _salvar_enderecos(credit_contract.address, mission_contract.address)
        print(f"[blockchain] CreditToken  → {credit_contract.address}")
        print(f"[blockchain] MissionLog   → {mission_contract.address}")

        # Autoriza o broker nos dois contratos
        _tx(credit_contract.functions.autorizarBroker(broker_account))
        _tx(mission_contract.functions.autorizarBroker(broker_account))
        print("[blockchain] Broker autorizado nos contratos.")

    _inicializado = True
    print("[blockchain] Pronto.\n")


# Funções do broker

def verificar_e_debitar(empresa: str, req_id: str) -> tuple:
    """
    Verifica se a empresa tem créditos suficientes e debita.
    Retorna (True, None) se ok, (False, motivo) se falhou.

    Parameters:
        empresa: endereço Ethereum da empresa (hex string)
        req_id:  ID da requisição de drone
    """
    _garantir_inicializado()
    try:
        empresa_addr = Web3.to_checksum_address(empresa)
        saldo_atual  = credit_contract.functions.saldo(empresa_addr).call()

        if saldo_atual < CREDIT_COST:
            return False, f"Créditos insuficientes ({saldo_atual}/{CREDIT_COST})"

        _tx(credit_contract.functions.debitar(empresa_addr, CREDIT_COST, req_id))
        print(f"[blockchain] Debitado {CREDIT_COST} ORC de {empresa_addr} para req {req_id}")
        return True, None

    except Exception as e:
        return False, str(e)


def registrar_laudo(req_id: str, drone_id: str, setor: str,
                    descricao: str, resultado: str) -> bool:
    """
    Registra o laudo de uma missão concluída na blockchain.
    Retorna True se registrado com sucesso.

    Parameters:
        req_id:    ID da requisição
        drone_id:  ID do drone
        setor:     setor do broker (ex: "A")
        descricao: descrição do incidente
        resultado: "ROTA_SEGURA" | "OBSTACULO_DETECTADO" | "DERIVA_RESOLVIDA"
    """
    _garantir_inicializado()
    try:
        _tx(mission_contract.functions.registrarLaudo(
            req_id, drone_id, setor, descricao, resultado
        ))
        print(f"[blockchain] Laudo registrado on-chain: {req_id}")
        return True
    except Exception as e:
        print(f"[blockchain] Erro ao registrar laudo {req_id}: {e}")
        return False


def emitir_creditos(empresa: str, quantidade: int) -> bool:
    """
    Emite créditos para uma empresa de navegação.
    Só pode ser chamado pela conta owner (conta 0 do Ganache).
    """
    _garantir_inicializado()
    try:
        empresa_addr = Web3.to_checksum_address(empresa)
        _tx(credit_contract.functions.mint(empresa_addr, quantidade))
        print(f"[blockchain] Emitidos {quantidade} ORC para {empresa_addr}")
        return True
    except Exception as e:
        print(f"[blockchain] Erro ao emitir créditos: {e}")
        return False


# Funções de consulta (broker e cliente)

def consultar_saldo(empresa: str) -> int:
    """Retorna o saldo de créditos de uma empresa."""
    _garantir_inicializado()
    try:
        return credit_contract.functions.saldo(
            Web3.to_checksum_address(empresa)
        ).call()
    except Exception:
        return -1


def obter_laudo(req_id: str) -> dict | None:
    """
    Retorna o laudo de uma missão pelo req_id.
    Retorna None se não encontrado.
    """
    _garantir_inicializado()
    try:
        drone_id, setor, descricao, resultado, timestamp, broker = \
            mission_contract.functions.obterLaudo(req_id).call()
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
    """Retorna o total de laudos registrados na blockchain."""
    _garantir_inicializado()
    try:
        return mission_contract.functions.totalLaudos().call()
    except Exception:
        return 0


def listar_laudos_recentes(n: int = 10) -> list:
    """
    Retorna os N laudos mais recentes como lista de dicts.
    """
    _garantir_inicializado()
    try:
        total = mission_contract.functions.totalLaudos().call()
        if total == 0:
            return []
        inicio = max(0, total - n)
        req_ids = mission_contract.functions.listarLaudos(inicio, total).call()
        laudos = []
        for rid in req_ids:
            l = obter_laudo(rid)
            if l:
                laudos.append(l)
        return list(reversed(laudos))  # mais recente primeiro
    except Exception as e:
        print(f"[blockchain] Erro ao listar laudos: {e}")
        return []


def laudos_por_setor(setor: str) -> list:
    """Retorna todos os laudos de um setor específico."""
    _garantir_inicializado()
    try:
        req_ids = mission_contract.functions.laudosPorSetor(setor).call()
        return [obter_laudo(rid) for rid in req_ids if obter_laudo(rid)]
    except Exception:
        return []


def endereco_contratos() -> dict:
    """Retorna os endereços dos contratos deployados."""
    _garantir_inicializado()
    return {
        "credit":  credit_contract.address,
        "mission": mission_contract.address,
    }


# Utilitários internos

def _tx(func, gas: int = 300_000):
    """Envia uma transação e aguarda o receipt."""
    tx_hash = func.transact({
        "from": broker_account,
        "gas":  gas,
    })
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def _compilar_abi(nome: str, source: str) -> list:
    """Compila o contrato e retorna apenas o ABI."""
    install_solc(SOLC_VERSION)
    compilado = compile_source(source, output_values=["abi", "bin"],
                               solc_version=SOLC_VERSION)
    chave = next(k for k in compilado if nome in k)
    return compilado[chave]["abi"]


def _deploy(nome: str, source: str):
    """Compila e faz deploy de um contrato. Retorna instância web3."""
    install_solc(SOLC_VERSION)
    compilado = compile_source(source, output_values=["abi", "bin"],
                               solc_version=SOLC_VERSION)
    chave     = next(k for k in compilado if nome in k)
    abi       = compilado[chave]["abi"]
    bytecode  = compilado[chave]["bin"]

    contrato  = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash   = contrato.constructor().transact({
        "from": broker_account,
        "gas":  3_000_000,
    })
    receipt   = w3.eth.wait_for_transaction_receipt(tx_hash)
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


def _salvar_enderecos(credit_addr: str, mission_addr: str):
    """Persiste endereços dos contratos em arquivo JSON."""
    with open(_ADDRESSES_FILE, "w") as f:
        json.dump({"credit": credit_addr, "mission": mission_addr}, f)


def _carregar_enderecos() -> dict | None:
    """Carrega endereços de contratos existentes, se disponíveis."""
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


def _garantir_inicializado():
    if not _inicializado:
        raise RuntimeError("blockchain.inicializar() não foi chamado")