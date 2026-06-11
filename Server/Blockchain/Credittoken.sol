// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * CreditToken — Moeda digital para requisição de drones
 * ======================================================
 * Token ERC-20 simplificado representando créditos operacionais.
 * Empresas de navegação precisam ter saldo suficiente para solicitar
 * escoltas de drones. O contrato garante que o mesmo saldo não pode
 * ser usado duas vezes (duplo gasto impossível pela própria blockchain).
 *
 * Funções principais:
 *   mint(endereco, quantidade)  — emite créditos para uma empresa (só owner)
 *   transfer(destino, valor)    — transfere créditos entre carteiras
 *   debitar(empresa, valor)     — debita créditos ao solicitar drone (só broker)
 *   saldo(endereco)             — consulta saldo de uma carteira
 */
contract CreditToken {

    string  public name     = "OrmuzCredit";
    string  public symbol   = "ORC";
    uint8   public decimals = 0;          // créditos inteiros, sem frações

    address public owner;                 // deployer — administrador do sistema
    uint256 public totalSupply;

    // saldo de cada carteira
    mapping(address => uint256) private _balances;

    // brokers autorizados a debitar créditos
    mapping(address => bool) public brokerAutorizado;

    // ── Eventos ────────────────────────────────────────────────
    event Transferencia(address indexed de, address indexed para, uint256 valor);
    event Emissao(address indexed para, uint256 valor);
    event Debito(address indexed empresa, uint256 valor, string req_id);
    event BrokerAutorizado(address indexed broker);

    // ── Modificadores ──────────────────────────────────────────
    modifier apenasOwner() {
        require(msg.sender == owner, "Apenas o owner pode executar isso");
        _;
    }

    modifier apenasBroker() {
        require(brokerAutorizado[msg.sender], "Apenas brokers autorizados");
        _;
    }

    // ── Constructor ────────────────────────────────────────────
    constructor() {
        owner = msg.sender;
    }

    // ── Administração ──────────────────────────────────────────

    /**
     * Autoriza um endereço (broker) a debitar créditos de empresas.
     * Chamado pelo owner ao registrar um novo broker no sistema.
     */
    function autorizarBroker(address broker) external apenasOwner {
        brokerAutorizado[broker] = true;
        emit BrokerAutorizado(broker);
    }

    /**
     * Emite créditos para uma empresa de navegação.
     * Equivalente a "depositar" créditos na conta da empresa.
     */
    function mint(address para, uint256 quantidade) external apenasOwner {
        require(para != address(0), "Endereco invalido");
        require(quantidade > 0, "Quantidade deve ser positiva");
        _balances[para] += quantidade;
        totalSupply      += quantidade;
        emit Emissao(para, quantidade);
    }

    // ── Operações de crédito ───────────────────────────────────

    /**
     * Transfere créditos entre carteiras.
     * Chamado pela empresa ao pagar por uma escolta.
     */
    function transfer(address para, uint256 valor) external returns (bool) {
        require(para != address(0), "Endereco invalido");
        require(_balances[msg.sender] >= valor, "Saldo insuficiente");
        _balances[msg.sender] -= valor;
        _balances[para]       += valor;
        emit Transferencia(msg.sender, para, valor);
        return true;
    }

    /**
     * Debita créditos de uma empresa ao despachar um drone.
     * Só pode ser chamado por brokers autorizados.
     * O req_id é registrado no evento para rastreabilidade.
     * Garante impossibilidade de duplo gasto: se saldo < custo,
     * a transação reverte e o drone não é despachado.
     */
    function debitar(
        address empresa,
        uint256 valor,
        string calldata req_id
    ) external apenasBroker returns (bool) {
        require(_balances[empresa] >= valor, "Creditos insuficientes");
        _balances[empresa] -= valor;
        emit Debito(empresa, valor, req_id);
        return true;
    }

    /**
     * Retorna o saldo de créditos de um endereço.
     */
    function saldo(address endereco) external view returns (uint256) {
        return _balances[endereco];
    }

    /**
     * Alias padrão ERC-20 para saldo.
     */
    function balanceOf(address endereco) external view returns (uint256) {
        return _balances[endereco];
    }
}