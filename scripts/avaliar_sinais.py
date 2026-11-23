#!/usr/bin/env python3
"""Confere os sinais de futuros publicados contra os candles de 1h da OKX.

Uso: python3 avaliar_sinais.py sinais.json > avaliacao.json

Entrada: uma lista JSON de sinais, como ficam no painel ({"id", "at", "ativo",
"direcao", "tipo", "entrada", "stop", "alvo1", "alvo2", ...}). Também aceita a
saída do ArtifactData (objetos com "id" e "data").

Saída: um objeto {id: {status, resultado_r, acionado_em, encerrado_em,
verificado_em, preco_atual, nota}} para cada sinal LONG/SHORT.

Regras (as mesmas para todo sinal, para o placar ser comparável):
- Imediato: a operação começa na hora do sinal, no preço de entrada.
- Condicional: começa quando o preço toca a entrada (máxima >= entrada num SHORT,
  mínima <= entrada num LONG). Se não tocar em 48 horas, o sinal "expirou" e não
  conta no placar.
- Gestão: no alvo 1 sai metade e o stop da outra metade vai para a entrada.
  A outra metade sai no alvo 2 ou na entrada.
- Se o stop e um alvo caem no mesmo candle de 1h, conta o stop (conservador).
- Operação aberta há mais de 7 dias é encerrada pelo último preço.
- Resultado em R: 1R é a distância entre a entrada e o stop.
  Stop = -1R. Alvo 1 e volta à entrada = metade de R1. Alvo 2 = (R1 + R2) / 2.
Só biblioteca padrão. Nada aqui é recomendação de investimento.
"""
import json, sys, time, urllib.request
from datetime import datetime, timezone

API = "https://www.okx.com/api/v5"
GATILHO_MAX_H = 48
OPERACAO_MAX_H = 7 * 24


def get(path):
    for _ in range(3):
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": "Mozilla/5.0 radar-avaliacao/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            if d.get("code") == "0":
                return d["data"]
        except Exception:
            pass
        time.sleep(1.5)
    return None


def candles_1h(ccy):
    rows = get(f"/market/candles?instId={ccy}-USDT-SWAP&bar=1H&limit=300")
    if not rows:
        return None
    out = [{"t": int(r[0]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])} for r in rows]
    return sorted(out, key=lambda x: x["t"])


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def rd(x):
    return None if x is None else round(x, 2)


def avaliar(s, cs, agora):
    e, st, a1, a2 = (float(s[k]) for k in ("entrada", "stop", "alvo1", "alvo2"))
    sgn = 1 if s["direcao"] == "LONG" else -1
    risco = abs(e - st)
    if risco == 0:
        return {"status": "invalido", "nota": "stop igual à entrada"}
    r1, r2 = abs(a1 - e) / risco, abs(a2 - e) / risco
    t0 = ms(s["at"])
    # só candles que começam depois do sinal: o candle em andamento na hora da publicação
    # pode ter tocado a entrada ou o stop antes de o sinal existir
    depois = [c for c in cs if c["t"] >= t0]
    if not depois:
        return {"status": "aguardando", "nota": "sem candles depois do sinal"}

    # 1) gatilho
    inicio = None
    if s.get("tipo") == "imediato":
        inicio = 0
    else:
        for i, c in enumerate(depois):
            if c["t"] - t0 > GATILHO_MAX_H * 3600_000:
                break
            if (sgn < 0 and c["h"] >= e) or (sgn > 0 and c["l"] <= e):
                inicio = i
                break
        if inicio is None:
            if agora - t0 > GATILHO_MAX_H * 3600_000:
                return {"status": "expirado", "resultado_r": 0, "nota": f"preço não chegou à entrada em {GATILHO_MAX_H}h"}
            return {"status": "aguardando", "nota": "gatilho ainda não aconteceu"}

    acionado = depois[inicio]["t"] if s.get("tipo") != "imediato" else t0
    stop, meia = st, False
    for c in depois[inicio:]:
        bateu_stop = (sgn > 0 and c["l"] <= stop) or (sgn < 0 and c["h"] >= stop)
        bateu_a1 = (sgn > 0 and c["h"] >= a1) or (sgn < 0 and c["l"] <= a1)
        bateu_a2 = (sgn > 0 and c["h"] >= a2) or (sgn < 0 and c["l"] <= a2)
        if not meia:
            if bateu_stop:
                return {"status": "stop", "resultado_r": -1.0, "acionado_em": iso(acionado), "encerrado_em": iso(c["t"])}
            if bateu_a1:
                meia, stop = True, e
                if bateu_a2:
                    return {"status": "alvo2", "resultado_r": rd((r1 + r2) / 2), "acionado_em": iso(acionado), "encerrado_em": iso(c["t"])}
                continue
        else:
            if bateu_stop:
                return {"status": "alvo1", "resultado_r": rd(r1 / 2), "acionado_em": iso(acionado), "encerrado_em": iso(c["t"]),
                        "nota": "metade no alvo 1, resto saiu na entrada"}
            if bateu_a2:
                return {"status": "alvo2", "resultado_r": rd((r1 + r2) / 2), "acionado_em": iso(acionado), "encerrado_em": iso(c["t"])}
        if c["t"] - acionado > OPERACAO_MAX_H * 3600_000:
            aberto = sgn * (c["c"] - e) / risco
            r = (r1 + aberto) / 2 if meia else aberto
            return {"status": "encerrado no prazo", "resultado_r": rd(r), "acionado_em": iso(acionado), "encerrado_em": iso(c["t"]),
                    "nota": "7 dias sem stop nem alvo 2: saída pelo preço"}
    ultimo = depois[-1]["c"]
    aberto = sgn * (ultimo - e) / risco
    return {"status": "alvo1 (aberto)" if meia else "aberto", "resultado_r": None,
            "r_flutuante": rd((r1 + aberto) / 2 if meia else aberto), "acionado_em": iso(acionado), "preco_atual": ultimo}


def main():
    sinais = json.load(open(sys.argv[1]))
    if isinstance(sinais, dict):
        sinais = sinais.get("documents") or sinais.get("docs") or list(sinais.values())
    sinais = [dict(x.get("data", {}), id=x["id"]) if "data" in x else x for x in sinais]
    agora = int(time.time() * 1000)
    cache, out = {}, {}
    for s in sinais:
        if s.get("direcao") not in ("LONG", "SHORT") or s.get("entrada") is None:
            continue
        if s.get("status") in ("stop", "alvo2", "alvo1", "expirado", "encerrado no prazo", "invalido"):
            continue  # já encerrado: não muda mais
        ccy = s["ativo"].upper()
        if ccy not in cache:
            cache[ccy] = candles_1h(ccy)
        if not cache[ccy]:
            out[s["id"]] = {"status": s.get("status") or "aguardando", "nota": "candles indisponíveis nesta rodada"}
            continue
        r = avaliar(s, cache[ccy], agora)
        r["verificado_em"] = iso(agora)
        out[s["id"]] = r
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
