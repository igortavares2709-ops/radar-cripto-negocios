#!/usr/bin/env python3
"""Carteira fictícia: aplica todos os sinais LONG/SHORT do painel numa conta de mentira.

Uso: python3 carteira.py sinais.json [--inicial 2000] [--risco 1] [--taxa 0.05] [--alav 5] > carteira.json

Entrada: a mesma lista de sinais que o avaliar_sinais.py usa, JÁ com o resultado da conferência
(status, resultado_r, r_flutuante, acionado_em, encerrado_em). Também aceita a saída do ArtifactData.

Regras (fixas, para a curva ser comparável rodada a rodada):
- Saldo inicial: US$ 2.000 (--inicial).
- Cada operação arrisca 1% do saldo do momento em que foi acionada (--risco). "Risco" é quanto se
  perde se o stop for atingido. Resultado da operação = risco em dólares x resultado em R.
- Tamanho da posição = risco / distância até o stop. Limitado a 5x o saldo (--alav);
  se passar, o risco é reduzido até caber.
- Taxas: 0,05% do tamanho da posição na entrada e 0,05% na saída (--taxa), descontadas do resultado.
- Sinal que expirou sem acionar não entra na conta. Sinal acionado e ainda aberto aparece em
  "abertas", com o resultado flutuante, mas só muda o saldo quando encerra.
Só biblioteca padrão. Nada aqui é recomendação de investimento.
"""
import argparse
import json
import sys
from dataclasses import dataclass
from typing import List, Optional

from radar_comum import ABERTOS, FINAIS_COM_RESULTADO, agora_iso, carregar_sinais, json_compacto, numero, rd2


@dataclass
class Regras:
    inicial: float = 2000.0
    risco_pct: float = 1.0      # % do saldo arriscado por operação
    taxa_pct: float = 0.05      # % do tamanho da posição, em cada lado
    alavancagem: float = 5.0    # tamanho máximo da posição, em vezes o saldo

    def descricao(self) -> str:
        return (f"risco de {self.risco_pct:g}% do saldo por operação, taxa de {self.taxa_pct:g}% por lado, "
                f"posição até {self.alavancagem:g}x o saldo; sinais expirados não contam; "
                "abertas só mexem no saldo quando encerram")


@dataclass
class Operacao:
    id: str
    ativo: str
    direcao: str
    tipo: Optional[str]
    status: str
    dist: float                 # distância até o stop, em fração do preço de entrada
    r: Optional[float]          # resultado final em R (encerradas)
    r_flut: Optional[float]     # resultado flutuante em R (abertas)
    ini: str                    # acionada em (ISO)
    fim: Optional[str]          # encerrada em (ISO), None se aberta
    risco_usd: float = 0.0
    tamanho_usd: float = 0.0
    entrou: bool = False


def operacoes_dos_sinais(sinais: List[dict]) -> List[Operacao]:
    """Só os sinais LONG/SHORT que foram acionados e estão encerrados ou abertos."""
    ops = []
    for s in sinais:
        if s.get("direcao") not in ("LONG", "SHORT") or not s.get("acionado_em"):
            continue
        st = s.get("status")
        if st not in FINAIS_COM_RESULTADO and st not in ABERTOS:
            continue
        try:
            entrada, stop = numero(s["entrada"]), numero(s["stop"])
        except (KeyError, TypeError, ValueError):
            continue
        if entrada <= 0 or entrada == stop:
            continue
        ops.append(Operacao(id=s["id"], ativo=s.get("ativo"), direcao=s["direcao"], tipo=s.get("tipo"), status=st,
                            dist=abs(entrada - stop) / entrada, r=s.get("resultado_r"), r_flut=s.get("r_flutuante"),
                            ini=s["acionado_em"], fim=s.get("encerrado_em") if st in FINAIS_COM_RESULTADO else None))
    return ops


def simular(ops: List[Operacao], regras: Regras) -> dict:
    """Percorre entradas e saídas em ordem. A entrada fixa o risco pelo saldo do momento; a saída
    muda o saldo. No mesmo horário, a saída vem antes da entrada."""
    eventos = [(o.ini, 1, o) for o in ops] + [(o.fim, 0, o) for o in ops if o.fim]
    eventos.sort(key=lambda e: (e[0], e[1]))

    saldo = pico = regras.inicial
    max_dd = 0.0
    inicio = min((o.ini for o in ops), default=None) or agora_iso()
    curva = [{"t": inicio, "saldo": rd2(saldo), "evento": "início"}]
    extrato = []
    for quando, eh_entrada, o in eventos:
        if eh_entrada:
            o.risco_usd = saldo * regras.risco_pct / 100
            o.tamanho_usd = o.risco_usd / o.dist
            if o.tamanho_usd > regras.alavancagem * saldo:
                o.tamanho_usd = regras.alavancagem * saldo
                o.risco_usd = o.tamanho_usd * o.dist
            o.entrou = True
            continue
        taxas = o.tamanho_usd * regras.taxa_pct / 100 * 2
        liquido = o.risco_usd * float(o.r or 0) - taxas
        saldo += liquido
        pico = max(pico, saldo)
        max_dd = max(max_dd, (pico - saldo) / pico * 100)
        curva.append({"t": quando, "saldo": rd2(saldo), "evento": f"{o.ativo} {o.direcao}", "r": o.r,
                      "resultado_usd": rd2(liquido)})
        extrato.append({"id": o.id, "ativo": o.ativo, "direcao": o.direcao, "tipo": o.tipo, "status": o.status,
                        "acionado_em": o.ini, "encerrado_em": quando, "r": o.r, "risco_usd": rd2(o.risco_usd),
                        "tamanho_usd": rd2(o.tamanho_usd), "taxas_usd": rd2(taxas), "resultado_usd": rd2(liquido),
                        "saldo_depois": rd2(saldo)})
    return {"saldo": saldo, "max_dd": max_dd, "curva": curva, "extrato": extrato}


def abertas(ops: List[Operacao], regras: Regras):
    """As operações ainda abertas, com o resultado flutuante (já descontada a taxa de entrada)."""
    lista, total = [], 0.0
    for o in ops:
        if o.fim or not o.entrou:
            continue
        flut = o.risco_usd * float(o.r_flut or 0) - o.tamanho_usd * regras.taxa_pct / 100
        total += flut
        lista.append({"id": o.id, "ativo": o.ativo, "direcao": o.direcao, "status": o.status,
                      "acionado_em": o.ini, "risco_usd": rd2(o.risco_usd), "tamanho_usd": rd2(o.tamanho_usd),
                      "r_flutuante": o.r_flut, "flutuante_usd": rd2(flut)})
    return lista, total


def placar(sinais: List[dict]) -> dict:
    """Placar de TODOS os sinais LONG/SHORT (os arquivados incluídos), com as mesmas contas do painel.
    Fica no documento da carteira porque o painel só lê os 1.000 registros mais antigos de cada coleção."""
    ops = [s for s in sinais if s.get("direcao") in ("LONG", "SHORT")]
    fechados = [s for s in ops if s.get("status") in FINAIS_COM_RESULTADO
                and isinstance(s.get("resultado_r"), (int, float)) and not isinstance(s.get("resultado_r"), bool)]
    acertos = sum(1 for s in fechados if s["resultado_r"] > 0)
    return {
        "publicados": len(ops),
        "acionados": sum(1 for s in ops if s.get("status") and s["status"] not in ("aguardando", "expirado", "invalido")),
        "encerrados": len(fechados),
        "abertos": sum(1 for s in ops if s.get("status") in ABERTOS),
        "expirados": sum(1 for s in ops if s.get("status") == "expirado"),
        "taxa_acerto": round(acertos / len(fechados) * 100) if fechados else None,
        "r_total": round(sum(s["resultado_r"] for s in fechados), 2),
    }


def carteira(sinais: List[dict], regras: Regras) -> dict:
    """O documento que vai para data/radar/carteira/atual."""
    ops = operacoes_dos_sinais(sinais)
    sim = simular(ops, regras)
    lista_abertas, flutuante = abertas(ops, regras)
    saldo, extrato = sim["saldo"], sim["extrato"]
    ganhos = sum(1 for x in extrato if x["resultado_usd"] > 0)
    return {
        "at": agora_iso(),
        "saldo_inicial": regras.inicial, "saldo": rd2(saldo), "saldo_com_abertas": rd2(saldo + flutuante),
        "resultado_usd": rd2(saldo - regras.inicial), "resultado_pct": rd2((saldo / regras.inicial - 1) * 100),
        "operacoes": len(extrato), "ganhos": ganhos, "perdas": len(extrato) - ganhos,
        "acerto_pct": rd2(100 * ganhos / len(extrato)) if extrato else None,
        "max_drawdown_pct": rd2(sim["max_dd"]), "abertas": len(lista_abertas), "flutuante_usd": rd2(flutuante),
        "regras": regras.descricao(),
        "placar": placar(sinais),
        "curva_json": json_compacto(sim["curva"]),
        "extrato_json": json_compacto(extrato),
        "abertas_json": json_compacto(lista_abertas),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sinais")
    ap.add_argument("--inicial", type=float, default=2000.0, help="saldo inicial em dólares")
    ap.add_argument("--risco", type=float, default=1.0, help="%% do saldo arriscado por operação")
    ap.add_argument("--taxa", type=float, default=0.05, help="%% de taxa por lado")
    ap.add_argument("--alav", type=float, default=5.0, help="tamanho máximo da posição, em vezes o saldo")
    a = ap.parse_args()
    regras = Regras(inicial=a.inicial, risco_pct=a.risco, taxa_pct=a.taxa, alavancagem=a.alav)
    json.dump(carteira(carregar_sinais(a.sinais), regras), sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
