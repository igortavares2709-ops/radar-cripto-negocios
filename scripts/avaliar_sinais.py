#!/usr/bin/env python3
"""Confere os sinais de futuros publicados contra os candles de 1h da OKX.

Uso: python3 avaliar_sinais.py sinais.json > avaliacao.json

Entrada: uma lista JSON de sinais, como ficam no painel ({"id", "at", "ativo",
"direcao", "tipo", "entrada", "stop", "alvo1", "alvo2", ...}). Também aceita a
saída do ArtifactData (objetos com "id" e "data").

Saída: um objeto {id: {status, resultado_r, acionado_em, encerrado_em,
verificado_em, preco_atual, nota}} para cada sinal LONG/SHORT ainda não encerrado.

Regras (as mesmas para todo sinal, para o placar ser comparável):
- Imediato: a operação começa na hora do sinal, no preço de entrada.
- Condicional: começa quando o preço toca a entrada (máxima >= entrada num SHORT,
  mínima <= entrada num LONG). Se não tocar em 48 horas, o sinal "expirou" e não
  conta no placar.
- Gestão (radar_comum.gerir): no alvo 1 sai metade e o stop da outra metade vai
  para a entrada. A outra metade sai no alvo 2 ou na entrada.
- Se o stop e um alvo caem no mesmo candle de 1h, conta o stop (conservador).
- Operação aberta há mais de 7 dias é encerrada pelo fechamento do candle em que o prazo venceu.
- Resultado em R: 1R é a distância entre a entrada e o stop.
  Stop = -1R. Alvo 1 e volta à entrada = metade de R1. Alvo 2 = (R1 + R2) / 2.
Só biblioteca padrão. Nada aqui é recomendação de investimento.
"""
import json
import sys
import time
from typing import Dict, List, Optional

from radar_comum import FINAIS, Plano, carregar_sinais, gerir, iso, ms, numero, okx, rd2

GATILHO_MAX_H = 48
OPERACAO_MAX_H = 7 * 24
HORA_MS = 3600_000
MAX_PAGINAS_HISTORICO = 30  # 100 candles por página: até ~4 meses além das 300 horas recentes


def candles_1h(ccy: str, desde: Optional[int] = None) -> Optional[Dict[str, List[float]]]:
    """Candles de 1h (o em andamento incluído), do mais antigo para o mais novo, como listas t/h/l/c.
    Começa pelas últimas 300 horas e, se `desde` (ms) for mais antigo, busca páginas do histórico."""
    linhas = okx.get(f"/market/candles?instId={ccy}-USDT-SWAP&bar=1H&limit=300")
    if not linhas:
        return None
    linhas = sorted(linhas, key=lambda r: int(r[0]))
    paginas = 0
    while desde is not None and int(linhas[0][0]) > desde and paginas < MAX_PAGINAS_HISTORICO:
        mais_antigo = int(linhas[0][0])
        velhas = okx.get(f"/market/history-candles?instId={ccy}-USDT-SWAP&bar=1H&after={mais_antigo}&limit=100")
        velhas = sorted((r for r in velhas or [] if int(r[0]) < mais_antigo), key=lambda r: int(r[0]))
        if not velhas:
            break
        linhas = velhas + linhas
        paginas += 1
    return {"t": [int(r[0]) for r in linhas], "h": [float(r[2]) for r in linhas],
            "l": [float(r[3]) for r in linhas], "c": [float(r[4]) for r in linhas]}


def plano_do_sinal(s: dict) -> Plano:
    e, st, a1, a2 = (numero(s[k]) for k in ("entrada", "stop", "alvo1", "alvo2"))
    return Plano(1 if s["direcao"] == "LONG" else -1, e, st, a1, a2)


def achar_gatilho(s: dict, p: Plano, k: dict, t0: int) -> Optional[int]:
    """Índice do candle em que a operação começa, ou None se o gatilho ainda não aconteceu.
    O imediato começa no primeiro candle; o condicional, no primeiro toque na entrada em até 48h."""
    if s.get("tipo") == "imediato":
        return 0
    for i, t in enumerate(k["t"]):
        if t - t0 > GATILHO_MAX_H * HORA_MS:
            return None
        if (p.sgn < 0 and k["h"][i] >= p.entrada) or (p.sgn > 0 and k["l"][i] <= p.entrada):
            return i
    return None


def avaliar(s: dict, candles: dict, agora: int) -> dict:
    """Situação e resultado de um sinal, pelos candles de 1h."""
    p = plano_do_sinal(s)
    if p.risco == 0:
        return {"status": "invalido", "nota": "stop igual à entrada"}
    t0 = ms(s["at"])
    if candles["t"][0] > t0:
        # os candles disponíveis começam depois do sinal: sem o começo da operação, não dá para conferir
        return {"status": s.get("status") or "aguardando",
                "nota": "candles desde a publicação indisponíveis nesta rodada; conferência adiada"}
    # só candles que começam depois do sinal: o candle em andamento na hora da publicação
    # pode ter tocado a entrada ou o stop antes de o sinal existir
    inicio_serie = next((i for i, t in enumerate(candles["t"]) if t >= t0), None)
    if inicio_serie is None:
        return {"status": "aguardando", "nota": "sem candles depois do sinal"}
    k = {campo: v[inicio_serie:] for campo, v in candles.items()}

    inicio = achar_gatilho(s, p, k, t0)
    if inicio is None:
        if agora - t0 > GATILHO_MAX_H * HORA_MS:
            return {"status": "expirado", "resultado_r": 0, "nota": f"preço não chegou à entrada em {GATILHO_MAX_H}h"}
        return {"status": "aguardando", "nota": "gatilho ainda não aconteceu"}

    acionado = t0 if s.get("tipo") == "imediato" else k["t"][inicio]
    saida = gerir(p, k["h"], k["l"], k["c"], inicio,
                  vencido=lambda j: k["t"][j] - acionado > OPERACAO_MAX_H * HORA_MS)
    base = {"acionado_em": iso(acionado)}
    fim = {"encerrado_em": iso(k["t"][saida.indice])} if saida.situacao != "aberto" else {}
    if saida.situacao == "stop":
        return {"status": "stop", "resultado_r": -1.0, **base, **fim}
    if saida.situacao == "alvo2":
        return {"status": "alvo2", "resultado_r": rd2(saida.r), **base, **fim}
    if saida.situacao == "alvo1":
        return {"status": "alvo1", "resultado_r": rd2(saida.r), **base, **fim,
                "nota": "metade no alvo 1, resto saiu na entrada"}
    if saida.situacao == "prazo":
        return {"status": "encerrado no prazo", "resultado_r": rd2(saida.r), **base, **fim,
                "nota": "7 dias sem stop nem alvo 2: saída pelo preço"}
    return {"status": "alvo1 (aberto)" if saida.meia else "aberto", "resultado_r": None,
            "r_flutuante": rd2(saida.r), **base, "preco_atual": k["c"][-1]}


def avaliar_todos(sinais: List[dict], agora: int) -> Dict[str, dict]:
    """Avalia os sinais LONG/SHORT ainda não encerrados. Baixa os candles de cada moeda uma vez só,
    desde o sinal pendente mais antigo dela. Um sinal com dados ruins não atrapalha os outros."""
    pendentes = [s for s in sinais if s.get("direcao") in ("LONG", "SHORT") and s.get("entrada") is not None
                 and s.get("status") not in FINAIS]  # encerrado não muda mais
    desde: Dict[str, int] = {}
    for s in pendentes:
        try:
            ccy, t0 = str(s["ativo"]).upper(), ms(s["at"])
        except Exception:
            continue
        desde[ccy] = min(desde.get(ccy, t0), t0)
    cache, out = {}, {}
    for s in pendentes:
        try:
            ccy = str(s["ativo"]).upper()
            if ccy not in cache:
                cache[ccy] = candles_1h(ccy, desde.get(ccy))
            if not cache[ccy]:
                r = {"status": s.get("status") or "aguardando", "nota": "candles indisponíveis nesta rodada"}
            else:
                r = avaliar(s, cache[ccy], agora)
        except Exception as e:
            print(f"[conferência] {s.get('id')}: {type(e).__name__}: {e}", file=sys.stderr)
            r = {"status": s.get("status") or "aguardando", "nota": f"sinal com dados inválidos, não conferido ({type(e).__name__})"}
        r["verificado_em"] = iso(agora)
        out[s["id"]] = r
    return out


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    resultado = avaliar_todos(carregar_sinais(sys.argv[1]), int(time.time() * 1000))
    json.dump(resultado, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
