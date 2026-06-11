// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * MissionLog — Registro imutável de laudos de missão
 * ====================================================
 * Cada vez que um drone conclui uma missão, o broker registra
 * um laudo on-chain. Uma vez gravado, o laudo não pode ser
 * alterado ou apagado — garantindo integridade das provas.
 *
 * Qualquer nação do consórcio pode consultar os laudos publicamente.
 *
 * Funções principais:
 *   registrarLaudo(...)   — grava laudo de missão concluída (só broker)
 *   obterLaudo(req_id)    — retorna laudo por ID de requisição
 *   totalLaudos()         — quantidade total de laudos registrados
 *   laudosPorSetor(setor) — lista req_ids de um setor específico
 */
contract MissionLog {

    address public owner;

    // brokers autorizados a registrar laudos
    mapping(address => bool) public brokerAutorizado;

    // ── Estrutura de laudo ────────────────────────────────────
    struct Laudo {
        string  req_id;        // ID da requisição (ex: "A-1716123456")
        string  drone_id;      // ID do drone que executou
        string  setor;         // setor do broker (A, B, C, D)
        string  descricao;     // descrição do incidente/resultado
        string  resultado;     // "ROTA_SEGURA", "OBSTACULO_DETECTADO", "DERIVA_RESOLVIDA"
        uint256 timestamp;     // timestamp Unix do registro
        address broker;        // endereço do broker que registrou
        bool    existe;        // flag de existência para lookup
    }

    // req_id → Laudo
    mapping(string => Laudo) private _laudos;

    // lista ordenada de req_ids por setor
    mapping(string => string[]) private _laudosPorSetor;

    // lista global de todos os req_ids
    string[] private _todosReqIds;

    // ── Eventos ────────────────────────────────────────────────
    event LaudoRegistrado(
        string  indexed req_id,
        string  drone_id,
        string  setor,
        string  resultado,
        uint256 timestamp
    );

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
     * Autoriza um broker a registrar laudos.
     */
    function autorizarBroker(address broker) external apenasOwner {
        brokerAutorizado[broker] = true;
        emit BrokerAutorizado(broker);
    }

    // ── Operações de laudo ─────────────────────────────────────

    /**
     * Registra o laudo de uma missão concluída.
     * Só pode ser chamado por brokers autorizados.
     * Um req_id não pode ser registrado duas vezes.
     *
     * @param req_id     ID único da requisição
     * @param drone_id   ID do drone que executou a missão
     * @param setor      Setor do broker (ex: "A")
     * @param descricao  Descrição do incidente original
     * @param resultado  Resultado da missão
     */
    function registrarLaudo(
        string calldata req_id,
        string calldata drone_id,
        string calldata setor,
        string calldata descricao,
        string calldata resultado
    ) external apenasBroker {
        require(bytes(req_id).length > 0, "req_id invalido");
        require(!_laudos[req_id].existe, "Laudo ja registrado para este req_id");

        _laudos[req_id] = Laudo({
            req_id:     req_id,
            drone_id:   drone_id,
            setor:      setor,
            descricao:  descricao,
            resultado:  resultado,
            timestamp:  block.timestamp,
            broker:     msg.sender,
            existe:     true
        });

        _laudosPorSetor[setor].push(req_id);
        _todosReqIds.push(req_id);

        emit LaudoRegistrado(req_id, drone_id, setor, resultado, block.timestamp);
    }

    // ── Consultas públicas ─────────────────────────────────────

    /**
     * Retorna um laudo pelo req_id.
     */
    function obterLaudo(string calldata req_id) external view returns (
        string memory drone_id,
        string memory setor,
        string memory descricao,
        string memory resultado,
        uint256 timestamp,
        address broker
    ) {
        require(_laudos[req_id].existe, "Laudo nao encontrado");
        Laudo storage l = _laudos[req_id];
        return (l.drone_id, l.setor, l.descricao, l.resultado, l.timestamp, l.broker);
    }

    /**
     * Retorna o total de laudos registrados.
     */
    function totalLaudos() external view returns (uint256) {
        return _todosReqIds.length;
    }

    /**
     * Retorna os req_ids de um setor específico.
     */
    function laudosPorSetor(string calldata setor) external view returns (string[] memory) {
        return _laudosPorSetor[setor];
    }

    /**
     * Retorna todos os req_ids registrados (paginado).
     * @param inicio  índice inicial
     * @param fim     índice final (exclusive)
     */
    function listarLaudos(uint256 inicio, uint256 fim)
        external view returns (string[] memory)
    {
        require(fim <= _todosReqIds.length, "Indice fora do limite");
        require(inicio < fim, "Intervalo invalido");
        string[] memory resultado = new string[](fim - inicio);
        for (uint256 i = inicio; i < fim; i++) {
            resultado[i - inicio] = _todosReqIds[i];
        }
        return resultado;
    }
}