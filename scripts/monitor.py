#!/usr/bin/env python3
"""Monitor dos trades: confere os sinais, atualiza a carteira e monta o quadro "ao vivo".

Uso:
  python3 monitor.py sinais.json --avaliacao avaliacao.json --carteira carteira.json \\
      --ao-vivo ao_vivo.json [--anterior ao_vivo_anterior.json]

Entrada: a lista de sinais do painel (coleção de futuros + LONG/SHORT do arquivo), como o
avaliar_sinais.py recebe. --anterior é o documento "ao vivo" da execução passada, para manter
o histórico dos últimos eventos.

Saídas:
- --avaliacao: o mesmo resultado do avaliar_sinais.py (campos a gravar em cada sinal).
- --carteira: o mesmo documento do carteira.py, já com a conferência aplicada.
- --ao-vivo: o quadro de cada trade em andamento (aguardando gatilho ou aberto) pelo preço
  atual da OKX: resultado flutuante em R e em US$, distância até o stop e até o próximo alvo,
  melhor e pior momento da operação (MFE/MAE, em R) e quando vence o prazo. Mais os eventos
  desta execução (acionado, alvo 1, alvo 2, stop, expirado, prazo), que viram aviso no celular.
Na saída padrão, um resumo curto com os eventos novos.

Só biblioteca padrão. Nada aqui é recomendação de investimento.
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

import avaliar_sinais as av
import carteira as ct
from radar_comum import ABERTOS, Plano, carregar_sinais, iso, json_compacto, ms, okx, rd2

ANDAMENTO = ("aguardando",) + tuple(sorted(ABERTOS))
MAX_EVENTOS = 30  # eventos guardados no quadro (os mais recentes)

TEXTO_EVENTO = {
    "aberto": "gatilho acionado: operação aberta",
    "alvo1 (aberto)": "bateu o alvo 1: metade realizada, stop do resto na entrada",
    "alvo1": "alvo 1 e o resto saiu na entrada",
    "alvo2": "bateu o alvo 2: operação encerrada com lucro",
    "stop": "bateu o stop: operação encerrada com prejuízo",
    "expirado": "expirou sem acionar o gatilho",
    "encerrado no prazo": "encerrada no prazo de 7 dias pelo preço",
    "invalido": "sinal inválido (stop igual à entrada)",
}


def aplicar(sinais: List[dict], avaliacao: Dict[str, dict]) -> List[dict]:
    """Cópia dos sinais com o resultado da conferência por cima."""
    return [dict(s, **avaliacao.get(s.get("id"), {})) for s in sinais]


def precos_atuais() -> Dict[str, float]:
    """Último preço de cada perpétuo USDT da OKX (uma chamada para todas as moedas)."""
    out = {}
    for t in okx.get("/market/tickers?instType=SWAP") or []:
        iid = t.get("instId", "")
        if iid.endswith("-USDT-SWAP"):
            try:
                out[iid.split("-")[0]] = float(t["last"])
            except (KeyError, TypeError, ValueError):
                pass
    return out


COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&include_tokens=top&symbols="


def ids_coingecko(simbolos: List[str], tentativas: int = 3, espera: float = 4.0) -> Dict[str, str]:
    """Código do CoinGecko de cada símbolo (ex.: VVV -> venice-token), para o painel buscar o preço
    ao vivo. Quando um símbolo tem várias moedas, vale a de maior valor de mercado. A API gratuita
    recusa pedidos em excesso (HTTP 429), então tenta de novo com espera. {} se não conseguir."""
    simbolos = sorted({s.lower() for s in simbolos if s})
    if not simbolos:
        return {}
    url = COINGECKO_MARKETS + urllib.parse.quote(",".join(simbolos))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 radar-cripto/1.0", "Accept": "application/json"})
    moedas = None
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                moedas = json.load(r)
            break
        except Exception:
            if i + 1 < tentativas:
                time.sleep(espera * (i + 1))
    if moedas is None:
        return {}
    melhor: Dict[str, dict] = {}
    for m in moedas if isinstance(moedas, list) else []:
        sim = str(m.get("symbol", "")).lower()
        if sim in simbolos and (sim not in melhor or (m.get("market_cap") or 0) > (melhor[sim].get("market_cap") or 0)):
            melhor[sim] = m
    return {sim.upper(): m["id"] for sim, m in melhor.items() if m.get("id")}


def excursao(p: Plano, candles: Optional[dict], desde_ms: int, preco: float):
    """Melhor (MFE) e pior (MAE) momento da operação desde que abriu, em R."""
    hs, ls = [preco], [preco]
    if candles:
        for t, h, l in zip(candles["t"], candles["h"], candles["l"]):
            if t >= desde_ms:  # acionado_em já é o começo do candle em que a operação abriu
                hs.append(h)
                ls.append(l)
    if p.sgn > 0:
        return (max(hs) - p.entrada) / p.risco, (min(ls) - p.entrada) / p.risco
    return (p.entrada - min(ls)) / p.risco, (p.entrada - max(hs)) / p.risco


def posicao(s: dict, preco: Optional[float], candles: Optional[dict], abertas: Dict[str, dict], taxa_pct: float) -> Optional[dict]:
    """O quadro ao vivo de um trade em andamento (None se faltar dado)."""
    try:
        p = av.plano_do_sinal(s)
        t0 = ms(s["at"])
    except Exception:
        return None
    if p.risco == 0:
        return None
    status = s.get("status") or "aguardando"
    if preco is None and candles:
        preco = candles["c"][-1]
    out = {"id": s["id"], "ativo": s.get("ativo"), "direcao": s["direcao"], "tipo": s.get("tipo"), "status": status,
           "at": s.get("at"), "entrada": p.entrada, "stop": p.stop, "alvo1": p.alvo1, "alvo2": p.alvo2,
           "preco": preco, "qualidade": s.get("qualidade"), "top10": s.get("top10")}
    if preco is None:
        return out
    if status == "aguardando":
        # quanto falta para o preço chegar na entrada (positivo = ainda longe)
        out["dist_entrada_pct"] = rd2(abs(p.entrada - preco) / preco * 100)
        out["prazo_fim"] = iso(t0 + av.GATILHO_MAX_H * av.HORA_MS)
        return out
    meia = status == "alvo1 (aberto)"
    stop_efetivo = p.entrada if meia else p.stop
    proximo_alvo = p.alvo2 if meia else p.alvo1
    r = p.r_aberto(preco, meia)
    acionado = s.get("acionado_em")
    ini = ms(acionado) if acionado else t0
    mfe, mae = excursao(p, candles, ini, preco)
    out.update({
        "stop_efetivo": stop_efetivo, "proximo_alvo": proximo_alvo, "acionado_em": acionado,
        "r_flutuante": rd2(r),
        "dist_stop_pct": rd2(p.sgn * (preco - stop_efetivo) / preco * 100),   # folga até o stop
        "dist_alvo_pct": rd2(p.sgn * (proximo_alvo - preco) / preco * 100),   # quanto falta para o alvo
        "mfe_r": rd2(mfe), "mae_r": rd2(mae),
        "prazo_fim": iso(ini + av.OPERACAO_MAX_H * av.HORA_MS),
    })
    a = abertas.get(s["id"])
    if a:
        out["risco_usd"], out["tamanho_usd"] = a["risco_usd"], a["tamanho_usd"]
        out["flutuante_usd"] = rd2(a["risco_usd"] * r - a["tamanho_usd"] * taxa_pct / 100)
    return out


def eventos(antes: List[dict], depois: List[dict], extrato: Dict[str, dict], agora: str) -> List[dict]:
    """Mudanças de situação desta execução (ex.: aguardando -> aberto, aberto -> stop)."""
    velho = {s.get("id"): s.get("status") for s in antes}
    out = []
    for s in depois:
        if s.get("direcao") not in ("LONG", "SHORT"):
            continue
        novo, anterior = s.get("status"), velho.get(s.get("id"))
        if not novo or novo == anterior or novo not in TEXTO_EVENTO or novo == "aguardando":
            continue
        ev = {"t": agora, "id": s["id"], "ativo": s.get("ativo"), "direcao": s["direcao"], "status": novo,
              "texto": TEXTO_EVENTO[novo]}
        if isinstance(s.get("resultado_r"), (int, float)):
            ev["r"] = s["resultado_r"]
        if s["id"] in extrato:
            ev["resultado_usd"] = extrato[s["id"]]["resultado_usd"]
        out.append(ev)
    return out


def ler_doc(caminho: Optional[str]) -> dict:
    """Documento salvo pelo ArtifactData (com ou sem o envelope {"data": ...}); {} se não houver."""
    if not caminho:
        return {}
    try:
        with open(caminho) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d.get("data", d) if isinstance(d, dict) and isinstance(d.get("data"), dict) else (d if isinstance(d, dict) else {})


def monitorar(sinais: List[dict], anterior: dict, agora_ms: Optional[int] = None) -> dict:
    agora_ms = agora_ms or int(time.time() * 1000)
    cache: dict = {}
    avaliacao = av.avaliar_todos(sinais, agora_ms, cache)
    depois = aplicar(sinais, avaliacao)
    regras = ct.Regras()
    cart = ct.carteira(depois, regras)
    abertas = {a["id"]: a for a in json.loads(cart["abertas_json"])}
    extrato = {x["id"]: x for x in json.loads(cart["extrato_json"])}
    precos = precos_atuais()
    posicoes = []
    for s in depois:
        if s.get("direcao") in ("LONG", "SHORT") and s.get("status") in ANDAMENTO:
            ccy = str(s.get("ativo", "")).upper()
            pos = posicao(s, precos.get(ccy), cache.get(ccy), abertas, regras.taxa_pct)
            if pos:
                posicoes.append(pos)
    posicoes.sort(key=lambda x: (x["status"] == "aguardando", str(x.get("at"))))
    # códigos do CoinGecko: reaproveita os já achados em execuções anteriores e só busca os novos
    conhecidos = dict(anterior.get("cg_ids") or {}) if isinstance(anterior.get("cg_ids"), dict) else {}
    try:
        for p in json.loads(anterior.get("posicoes_json") or "[]"):
            if isinstance(p, dict) and p.get("cg_id") and p.get("ativo"):
                conhecidos.setdefault(str(p["ativo"]).upper(), p["cg_id"])
    except ValueError:
        pass
    faltam = sorted({str(p.get("ativo", "")).upper() for p in posicoes} - set(conhecidos))
    if faltam:
        conhecidos.update(ids_coingecko(faltam))
    sem_codigo = sorted({str(p.get("ativo", "")).upper() for p in posicoes} - set(conhecidos))
    for p in posicoes:
        p["cg_id"] = conhecidos.get(str(p.get("ativo", "")).upper())
    agora = iso(agora_ms)
    novos = eventos(sinais, depois, extrato, agora)
    try:
        historico = json.loads(anterior.get("eventos_json") or "[]")
    except ValueError:
        historico = []
    historico = (novos + [e for e in historico if isinstance(e, dict)])[:MAX_EVENTOS]
    flut = sum(p.get("flutuante_usd") or 0 for p in posicoes)
    ao_vivo = {
        "at": agora, "saldo": cart["saldo"], "saldo_com_abertas": rd2(cart["saldo"] + flut), "flutuante_usd": rd2(flut),
        "abertas": sum(1 for p in posicoes if p["status"] != "aguardando"),
        "aguardando": sum(1 for p in posicoes if p["status"] == "aguardando"),
        "precos_de": "OKX, último negócio", "novos_eventos": len(novos),
        "posicoes_json": json_compacto(posicoes), "eventos_json": json_compacto(historico),
        # memória símbolo -> código do CoinGecko (só dos que existem; fica pequena)
        "cg_ids": {k: v for k, v in sorted(conhecidos.items()) if v},
    }
    return {"avaliacao": avaliacao, "carteira": cart, "ao_vivo": ao_vivo, "novos": novos, "sem_codigo": sem_codigo}


def resumo_texto(r: dict) -> str:
    v = r["ao_vivo"]
    linhas = [f"Monitor {v['at']}: saldo US$ {v['saldo']:.2f} (com abertas US$ {v['saldo_com_abertas']:.2f}), "
              f"{v['abertas']} aberta(s), {v['aguardando']} aguardando gatilho."]
    for e in r["novos"]:
        extra = f" ({e['r']:+.2f}R" + (f", US$ {e['resultado_usd']:+.2f}" if "resultado_usd" in e else "") + ")" if "r" in e else ""
        linhas.append(f"- {e['ativo']} {e['direcao']}: {e['texto']}{extra}")
    if not r["novos"]:
        linhas.append("Nenhuma mudança de situação.")
    if r.get("sem_codigo"):
        linhas.append("Aviso: sem código do CoinGecko para " + ", ".join(r["sem_codigo"])
                      + " (a aba Ao vivo mostra o preço da conferência para essas moedas).")
    return "\n".join(linhas)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sinais")
    ap.add_argument("--avaliacao", required=True)
    ap.add_argument("--carteira", required=True)
    ap.add_argument("--ao-vivo", required=True)
    ap.add_argument("--anterior")
    a = ap.parse_args()
    r = monitorar(carregar_sinais(a.sinais), ler_doc(a.anterior))
    for caminho, obj in ((a.avaliacao, r["avaliacao"]), (a.carteira, r["carteira"]), (a.ao_vivo, r["ao_vivo"])):
        with open(caminho, "w") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    print(resumo_texto(r))


if __name__ == "__main__":
    main()
