#!/usr/bin/env python3
"""Carteira fictícia: aplica todos os sinais LONG/SHORT do painel numa conta de mentira.

Uso: python3 carteira.py sinais.json [--inicial 2000] [--risco 1] [--taxa 0.05] > carteira.json

Entrada: a mesma lista de sinais que o avaliar_sinais.py usa, JÁ com o resultado da conferência
(status, resultado_r, r_flutuante, acionado_em, encerrado_em). Também aceita a saída do ArtifactData.

Regras (fixas, para a curva ser comparável rodada a rodada):
- Saldo inicial: US$ 2.000 (--inicial).
- Cada operação arrisca 1% do saldo do momento em que foi acionada (--risco). "Risco" é quanto se
  perde se o stop for atingido. Resultado da operação = risco em dólares x resultado em R.
- Tamanho da posição = risco / distância até o stop. Limitado a 5x o saldo (alavancagem máxima);
  se passar, o risco é reduzido até caber.
- Taxas: 0,05% do tamanho da posição na entrada e 0,05% na saída (--taxa), descontadas do resultado.
- Sinal que expirou sem acionar não entra na conta. Sinal acionado e ainda aberto aparece em
  "abertas", com o resultado flutuante, mas só muda o saldo quando encerra.
Só biblioteca padrão. Nada aqui é recomendação de investimento.
"""
import argparse, json, sys, time

ENCERRADOS = {"stop", "alvo1", "alvo2", "encerrado no prazo"}
ABERTOS = {"aberto", "alvo1 (aberto)"}


def carregar(caminho):
    sinais = json.load(open(caminho))
    if isinstance(sinais, dict):
        sinais = sinais.get("documents") or sinais.get("docs") or list(sinais.values())
    return [dict(x.get("data", {}), id=x["id"]) if "data" in x else x for x in sinais]


def r2(x):
    return round(x, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sinais")
    ap.add_argument("--inicial", type=float, default=2000.0)
    ap.add_argument("--risco", type=float, default=1.0, help="%% do saldo arriscado por operação")
    ap.add_argument("--taxa", type=float, default=0.05, help="%% de taxa por lado")
    ap.add_argument("--alav", type=float, default=5.0, help="tamanho máximo da posição, em vezes o saldo")
    a = ap.parse_args()

    ops = []
    for s in carregar(a.sinais):
        if s.get("direcao") not in ("LONG", "SHORT") or not s.get("acionado_em"):
            continue
        st = s.get("status")
        if st not in ENCERRADOS and st not in ABERTOS:
            continue
        try:
            e, sp = float(s["entrada"]), float(s["stop"])
        except (KeyError, TypeError, ValueError):
            continue
        if e <= 0 or e == sp:
            continue
        ops.append({"id": s["id"], "ativo": s.get("ativo"), "direcao": s["direcao"], "tipo": s.get("tipo"),
                    "entrada": e, "dist": abs(e - sp) / e, "status": st,
                    "r": s.get("resultado_r"), "r_flut": s.get("r_flutuante"),
                    "ini": s["acionado_em"], "fim": s.get("encerrado_em") if st in ENCERRADOS else None})

    # eventos em ordem: entradas (fixam o risco pelo saldo do momento) e saídas (mudam o saldo)
    ev = []
    for o in ops:
        ev.append((o["ini"], 1, o))
        if o["fim"]:
            ev.append((o["fim"], 0, o))  # no mesmo horário, a saída vem antes da entrada
    ev.sort(key=lambda x: (x[0], x[1]))

    saldo = pico = a.inicial
    max_dd = 0.0
    curva = [{"t": min((o["ini"] for o in ops), default=None) or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "saldo": r2(saldo), "evento": "início"}]
    extrato = []
    for t, tipo, o in ev:
        if tipo == 1:
            risco = saldo * a.risco / 100
            tam = risco / o["dist"]
            if tam > a.alav * saldo:
                tam = a.alav * saldo
                risco = tam * o["dist"]
            o["risco_usd"], o["tamanho_usd"], o["saldo_entrada"] = risco, tam, saldo
            continue
        bruto = o["risco_usd"] * float(o["r"] or 0)
        taxas = o["tamanho_usd"] * a.taxa / 100 * 2
        liq = bruto - taxas
        saldo += liq
        pico = max(pico, saldo)
        max_dd = max(max_dd, (pico - saldo) / pico * 100)
        curva.append({"t": t, "saldo": r2(saldo), "evento": f"{o['ativo']} {o['direcao']}", "r": o["r"], "resultado_usd": r2(liq)})
        extrato.append({"id": o["id"], "ativo": o["ativo"], "direcao": o["direcao"], "tipo": o["tipo"], "status": o["status"],
                        "acionado_em": o["ini"], "encerrado_em": t, "r": o["r"], "risco_usd": r2(o["risco_usd"]),
                        "tamanho_usd": r2(o["tamanho_usd"]), "taxas_usd": r2(taxas), "resultado_usd": r2(liq),
                        "saldo_depois": r2(saldo)})

    abertas = []
    flut_total = 0.0
    for o in ops:
        if o["fim"] or "risco_usd" not in o:
            continue
        flut = o["risco_usd"] * float(o["r_flut"] or 0) - o["tamanho_usd"] * a.taxa / 100
        flut_total += flut
        abertas.append({"id": o["id"], "ativo": o["ativo"], "direcao": o["direcao"], "status": o["status"],
                        "acionado_em": o["ini"], "risco_usd": r2(o["risco_usd"]), "tamanho_usd": r2(o["tamanho_usd"]),
                        "r_flutuante": o["r_flut"], "flutuante_usd": r2(flut)})

    ganhos = [x for x in extrato if x["resultado_usd"] > 0]
    doc = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "saldo_inicial": a.inicial, "saldo": r2(saldo), "saldo_com_abertas": r2(saldo + flut_total),
        "resultado_usd": r2(saldo - a.inicial), "resultado_pct": r2((saldo / a.inicial - 1) * 100),
        "operacoes": len(extrato), "ganhos": len(ganhos), "perdas": len(extrato) - len(ganhos),
        "acerto_pct": r2(100 * len(ganhos) / len(extrato)) if extrato else None,
        "max_drawdown_pct": r2(max_dd), "abertas": len(abertas), "flutuante_usd": r2(flut_total),
        "regras": f"risco de {a.risco:g}% do saldo por operação, taxa de {a.taxa:g}% por lado, posição até {a.alav:g}x o saldo; "
                  "sinais expirados não contam; abertas só mexem no saldo quando encerram",
        "curva_json": json.dumps(curva, ensure_ascii=False, separators=(",", ":")),
        "extrato_json": json.dumps(extrato, ensure_ascii=False, separators=(",", ":")),
        "abertas_json": json.dumps(abertas, ensure_ascii=False, separators=(",", ":")),
    }
    json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
