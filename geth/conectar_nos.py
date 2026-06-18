#!/usr/bin/env python3
"""
geth/conectar_nos.py
Conecta os 3 nós Geth entre si via admin_addPeer.
Execute após subir os containers.
"""

import json, time, subprocess, sys

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

NOS = [
    {"nome": "geth-node1", "rpc": "http://localhost:8601"},
    {"nome": "geth-node2", "rpc": "http://localhost:8602"},
    {"nome": "geth-node3", "rpc": "http://localhost:8603"},
]


def rpc(url, method, params=None):
    try:
        r = requests.post(url, json={
            "jsonrpc": "2.0", "method": method,
            "params": params or [], "id": 1
        }, timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def ip_container(nome):
    r = subprocess.run(
        ["docker", "inspect", "-f",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", nome],
        capture_output=True, text=True
    )
    return r.stdout.strip()


def obter_enode(no):
    resp = rpc(no["rpc"], "admin_nodeInfo")
    if "error" in resp:
        raise RuntimeError(resp["error"])
    enode = resp["result"]["enode"]
    # Substitui IP por IP real do container
    ip   = ip_container(no["nome"])
    partes = enode.split("@")
    porta  = partes[1].split(":")[1].split("?")[0]
    return f"{partes[0]}@{ip}:{porta}"


def main():
    print("Aguardando nós iniciarem...")
    time.sleep(5)

    enodes = {}
    for no in NOS:
        for t in range(15):
            try:
                enode = obter_enode(no)
                enodes[no["nome"]] = enode
                print(f"✓ {no['nome']}: {enode[:70]}...")
                break
            except Exception as e:
                print(f"  {no['nome']} tentativa {t+1}/15: {e}")
                time.sleep(4)
        else:
            print(f"✗ {no['nome']} não respondeu. Abortando.")
            sys.exit(1)

    with open("geth/enodes.json", "w") as f:
        json.dump(enodes, f, indent=2)

    print("\nConectando peers...")
    for no in NOS:
        for peer_nome, enode_peer in enodes.items():
            if peer_nome == no["nome"]:
                continue
            resp = rpc(no["rpc"], "admin_addPeer", [enode_peer])
            ok   = resp.get("result", False)
            print(f"  {'✓' if ok else '✗'} {no['nome']} → {peer_nome}")

    print("\nVerificando (aguardando 5s)...")
    time.sleep(5)
    for no in NOS:
        r     = rpc(no["rpc"], "net_peerCount")
        count = int(r.get("result", "0x0"), 16)
        bloco = int(rpc(no["rpc"], "eth_blockNumber").get("result", "0x0"), 16)
        print(f"  {'✓' if count >= 1 else '⚠'} {no['nome']}: {count} peer(s), bloco #{bloco}")

    print("\n✓ Rede Geth pronta.")


if __name__ == "__main__":
    main()