#!/usr/bin/env python3
"""Análise técnica multi-timeframe de futuros perpétuos (dados públicos da OKX).

Uso:
  python3 analise_tecnica.py BTC ETH SOL
      analisa só as moedas pedidas e imprime o JSON completo.
  python3 analise_tecnica.py --todas [--min-vol 1000000] [--saida ta.json] [--scanner scanner.json] [--top 12]
      varre TODAS as criptos com perpétuo USDT na OKX (instCategory 1, sem ações nem commodities,
      sem stablecoins) com volume de 24h acima de --min-vol dólares. Grava o JSON completo em --saida,
      a tabela compacta do radar em --scanner e imprime um resumo com as 10 melhores oportunidades.

Para cada moeda: tendência, médias, RSI, MACD, ATR, suportes e resistências em 1D, 4H e 1H, mais
funding, open interest e contas long/short; um setup (LONG/SHORT imediato ou plano condicional);
um backtest do mesmo plano nos candles 4H da própria moeda; e uma nota de qualidade de 0 a 100.

Top 10: as 10 maiores notas entre as moedas com plano, mais de US$ 5 milhões por dia e backtest
que não deu prejuízo. A nota junta o backtest (rendimento médio e acerto), a confluência dos tempos
gráficos, o RSI (entrada esticada perde pontos), a liquidez e o posicionamento (funding e contas
long/short contra a multidão).

Só biblioteca padrão. Nada aqui é recomendação de investimento.
"""
import argparse
import json
import math
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from indicadores import atr, atr_serie, ema, rsi, swings
from radar_comum import Plano, agora_iso, gerir, json_compacto, okx, rd_sig

# ---------------------------------------------------------------- parâmetros

TEMPOS_GRAFICOS = {"1D": 300, "4H": 300, "1H": 300}  # tempo gráfico -> quantos candles baixar
MIN_CANDLES = 60
ESTAVEIS = {"USDC", "USDT", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USDD", "BUSD", "USD1", "RLUSD", "USDG"}

# setup
PONTUACAO_SINAL = 5        # |pontuação| para LONG/SHORT imediato
PONTUACAO_PLANO = 3        # |pontuação| para plano condicional (e para "viés" no resumo)
RSI_4H_ESTICADO = (28, 72)          # (sobrevendido, sobrecomprado) no 4H: esperar repique/recuo
RSI_1D_EXTREMO = (25, 75)           # sobrevenda/sobrecompra extrema no diário
FUNDING_ALTO, FUNDING_NEGATIVO = 0.03, -0.01   # % a cada 8h
STOP_ATR = 1.5                      # stop do imediato: 1,5 ATR do 4H
ALVOS_R = (1.5, 3.0)                # alvos do imediato em múltiplos do risco
STOP_NO_NIVEL = (1.0, 2.5, 0.3)     # usa o suporte/resistência se estiver entre 1 e 2,5 ATR (+0,3 ATR de folga)
PERDA_MAX_MARGEM_PCT, ALAVANCAGEM_MAX = 2, 5
# plano condicional: entrada na EMA20 do 4H (ou meio ATR além do preço), stop e alvos em ATR
ZONA_ATR, STOP_COND_ATR, ALVOS_COND_ATR = 0.5, 1.5, (2.25, 4.5)

# backtest (mesmas regras do avaliar_sinais.py, em candles 4H)
BT_INICIO = 60                      # primeiro candle testado (as médias precisam de histórico)
BT_GATILHO_CANDLES = 12             # 48h para o condicional acionar
BT_PRAZO_CANDLES = 42               # 7 dias de operação

# Top 10
TOP_N = 10
VOLUME_MIN_TOP_MI = 5               # US$ milhões em 24h
BT_MIN_OPERACOES = 3                # com menos que isso, o backtest não exclui a moeda

# ---------------------------------------------------------------- candles e indicadores por tempo gráfico


def baixar_candles(inst: str, bar: str, limite: int) -> Optional[Dict[str, List[float]]]:
    """Candles fechados, do mais antigo para o mais novo, como listas o/h/l/c/v."""
    linhas = okx.get(f"/market/candles?instId={inst}&bar={bar}&limit={min(limite, 300)}")
    if not linhas:
        return None
    vistos, fechados = set(), []
    for r in linhas:
        if r[0] not in vistos and r[8] == "1":  # r[8] == "1": candle fechado
            vistos.add(r[0])
            fechados.append(r)
    fechados.sort(key=lambda r: int(r[0]))
    o, h, l, c, v = ([float(r[i]) for r in fechados] for i in (1, 2, 3, 4, 5))
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


def analisar_tempo_grafico(k: Dict[str, List[float]]) -> Optional[dict]:
    """Resumo técnico de uma série de candles (None se houver menos de MIN_CANDLES)."""
    h, l, c, v = k["h"], k["l"], k["c"], k["v"]
    if len(c) < MIN_CANDLES:
        return None
    e20, e50 = ema(c, 20), ema(c, 50)
    e200 = ema(c, 200) if len(c) >= 200 else None
    macd = [a - b for a, b in zip(ema(c, 12), ema(c, 26))]
    sinal_macd = ema(macd, 9)
    ultimo = c[-1]
    media20 = sum(c[-20:]) / 20
    desvio20 = (sum((x - media20) ** 2 for x in c[-20:]) / 20) ** 0.5
    topos, fundos = swings(h, l)
    vol_media50 = sum(v[-50:]) / 50
    tendencia = (1 if ultimo > e20[-1] else -1) + (1 if e20[-1] > e50[-1] else -1)
    if e200:
        tendencia += 1 if ultimo > e200[-1] else -1
    return {
        "fechamento": ultimo, "ema20": rd_sig(e20[-1]), "ema50": rd_sig(e50[-1]),
        "ema200": rd_sig(e200[-1]) if e200 else None,
        "rsi14": round(rsi(c), 1),
        "macd_hist": rd_sig(macd[-1] - sinal_macd[-1]), "macd_hist_ant": rd_sig(macd[-2] - sinal_macd[-2]),
        "atr14": rd_sig(atr(h, l, c)),
        "bollinger": [rd_sig(media20 - 2 * desvio20), rd_sig(media20 + 2 * desvio20)],
        "resistencias": sorted({rd_sig(x) for x in topos if x > ultimo})[:3],
        "suportes": sorted({rd_sig(x) for x in fundos if x < ultimo}, reverse=True)[:3],
        "max_20": max(h[-20:]), "min_20": min(l[-20:]),
        # moeda recém-listada pode ter volume zero: sem média, sem razão
        "volume_vs_media50": rd_sig((sum(v[-5:]) / 5) / vol_media50) if vol_media50 > 0 else None,
        "variacao_10_candles_pct": rd_sig((ultimo / c[-11] - 1) * 100) if c[-11] else None,
        "tendencia": tendencia,  # -3 (baixa forte) a +3 (alta forte)
    }


def _numero(x) -> Optional[float]:
    """float de um campo da OKX, ou None se vier vazio ou estranho."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def derivativos(ccy: str, inst: str) -> dict:
    """Funding atual, open interest (e variação em 7 dias) e relação de contas long/short.
    Um campo que a OKX devolver vazio ou zerado só fica de fora; não derruba a moeda."""
    out = {}
    f = okx.get(f"/public/funding-rate?instId={inst}")
    fr = _numero(f[0].get("fundingRate")) if f else None
    if fr is not None:
        out["funding_8h_pct"] = round(fr * 100, 4)
    oi = okx.get(f"/rubik/stat/contracts/open-interest-volume?ccy={ccy}&period=1D")
    if oi and len(oi) > 7:
        agora, antes = _numero(oi[0][1]), _numero(oi[7][1])
        if agora is not None:
            out["oi_usd"] = agora
            if antes:
                out["oi_var_7d_pct"] = round((agora / antes - 1) * 100, 2)
    ls = okx.get(f"/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=1H")
    razao = _numero(ls[0][1]) if ls else None
    if razao is not None:
        out["long_short_contas"] = razao
    return out

# ---------------------------------------------------------------- setup


def pontuar(t1d: dict, t4h: dict, t1h: dict, d: dict):
    """Pontuação de -9 a +9 (tendência 1D e 4H, MACD 4H, tendência 1H, funding) e os motivos."""
    score, motivos = 0, []
    score += t1d["tendencia"]
    motivos.append(f"tendência 1D {t1d['tendencia']:+d}")
    score += t4h["tendencia"]
    motivos.append(f"tendência 4H {t4h['tendencia']:+d}")
    if t4h["macd_hist"] > 0 and t4h["macd_hist"] > t4h["macd_hist_ant"]:
        score += 1
        motivos.append("MACD 4H virando para cima")
    if t4h["macd_hist"] < 0 and t4h["macd_hist"] < t4h["macd_hist_ant"]:
        score -= 1
        motivos.append("MACD 4H ganhando força para baixo")
    if t1h["tendencia"] > 0:
        score += 1
    if t1h["tendencia"] < 0:
        score -= 1
    fr = d.get("funding_8h_pct")
    if fr is not None:
        if fr > FUNDING_ALTO:
            score -= 1
            motivos.append("funding alto: comprados lotados")
        if fr < FUNDING_NEGATIVO:
            score += 1
            motivos.append("funding negativo: vendidos lotados (risco de short squeeze)")
    return score, motivos


def motivo_esticado(direcao: str, t1d: dict, t4h: dict) -> Optional[str]:
    """Por que NÃO entrar agora (preço esticado demais na direção do sinal), ou None.
    Vale o primeiro motivo encontrado: RSI do 4H antes do RSI diário."""
    r4, r1d = t4h["rsi14"], t1d["rsi14"]
    if direcao == "LONG" and r4 > RSI_4H_ESTICADO[1]:
        return "RSI 4H sobrecomprado: esperar recuo"
    if direcao == "SHORT" and r4 < RSI_4H_ESTICADO[0]:
        return "RSI 4H sobrevendido: esperar repique"
    if direcao == "SHORT" and r1d < RSI_1D_EXTREMO[0]:
        return f"RSI diário {r1d} (sobrevenda extrema): vender aqui é correr atrás do preço"
    if direcao == "LONG" and r1d > RSI_1D_EXTREMO[1]:
        return f"RSI diário {r1d} (sobrecompra extrema)"
    return None


def plano_condicional(score: int, preco: float, t4h: dict) -> Optional[dict]:
    """Operar a favor da tendência só depois de um repique/recuo até a EMA20 do 4H."""
    a = t4h["atr14"]
    if score <= -PONTUACAO_PLANO:
        z = max(t4h["ema20"], preco + ZONA_ATR * a)
        return {"direcao": "SHORT", "gatilho": f"repique até a zona de {rd_sig(z)} com rejeição (candle 4H fechando abaixo)",
                "entrada": rd_sig(z), "stop": rd_sig(z + STOP_COND_ATR * a),
                "alvo1": rd_sig(z - ALVOS_COND_ATR[0] * a), "alvo2": rd_sig(z - ALVOS_COND_ATR[1] * a)}
    if score >= PONTUACAO_PLANO:
        z = min(t4h["ema20"], preco - ZONA_ATR * a)
        return {"direcao": "LONG", "gatilho": f"recuo até a zona de {rd_sig(z)} com defesa (candle 4H fechando acima)",
                "entrada": rd_sig(z), "stop": rd_sig(z - STOP_COND_ATR * a),
                "alvo1": rd_sig(z + ALVOS_COND_ATR[0] * a), "alvo2": rd_sig(z + ALVOS_COND_ATR[1] * a)}
    return None


def niveis_imediatos(direcao: str, preco: float, t4h: dict) -> dict:
    """Entrada no preço, stop em 1,5 ATR (ou além do suporte/resistência próximo) e alvos em 1,5R e 3R."""
    sgn = 1 if direcao == "LONG" else -1
    a = t4h["atr14"]
    stop = preco - sgn * STOP_ATR * a
    niveis = t4h["suportes"] if sgn > 0 else t4h["resistencias"]
    if niveis:
        perto_min, perto_max, folga = STOP_NO_NIVEL
        alternativo = niveis[0] - sgn * folga * a
        if perto_min * a <= abs(preco - alternativo) < perto_max * a and (alternativo - preco) * sgn < 0:
            stop = alternativo
    risco = abs(preco - stop)
    risco_pct = risco / preco * 100
    return {"entrada": rd_sig(preco), "stop": rd_sig(stop),
            "alvo1": rd_sig(preco + sgn * ALVOS_R[0] * risco), "alvo2": rd_sig(preco + sgn * ALVOS_R[1] * risco),
            "risco_pct": rd_sig(risco_pct),
            # alavancagem para perder no máximo ~2% da margem se o stop for atingido, limitada a 5x
            "alavancagem_max": max(1, min(ALAVANCAGEM_MAX, int(PERDA_MAX_MARGEM_PCT / max(risco_pct, 0.01))))}


def setup(tfs: dict, d: dict) -> dict:
    """Direção sugerida (LONG, SHORT ou SEM SINAL), com níveis do imediato ou um plano condicional."""
    t1d, t4h, t1h = (tfs.get(k) for k in ("1D", "4H", "1H"))
    if not (t1d and t4h and t1h):
        return {"direcao": "SEM SINAL", "motivo": "dados insuficientes"}
    if not t4h["atr14"] or t4h["atr14"] <= 0:
        # preço parado (ATR zero): stop e alvos cairiam em cima da entrada
        return {"direcao": "SEM SINAL", "motivo": "preço sem oscilação no 4H (ATR zero)"}
    score, motivos = pontuar(t1d, t4h, t1h, d)
    direcao = "LONG" if score >= PONTUACAO_SINAL else "SHORT" if score <= -PONTUACAO_SINAL else "SEM SINAL"
    esticado = motivo_esticado(direcao, t1d, t4h)
    if esticado:
        direcao = "SEM SINAL"
        motivos.append(esticado)
    preco = t4h["fechamento"]
    out = {"direcao": direcao, "pontuacao": score, "motivos": motivos, "preco": preco}
    if direcao == "SEM SINAL":
        pc = plano_condicional(score, preco, t4h)
        if pc:
            out["plano_condicional"] = pc
        return out
    out.update(niveis_imediatos(direcao, preco, t4h))
    return out

# ---------------------------------------------------------------- backtest


def backtest(k: Dict[str, List[float]], direcao: str, tipo: str) -> dict:
    """Testa o mesmo tipo de plano nos candles 4H da própria moeda, sem olhar o futuro.

    Em cada candle com tendência a favor (preço e EMA20 do lado da direção, EMA20 sobre a EMA50),
    abre o plano: imediato no fechamento, ou condicional na zona da EMA20 se o preço chegar lá em
    até 48h. Uma operação por vez, com a gestão do radar_comum.gerir e prazo de 7 dias.
    Devolve {"n", "acerto" (% com lucro), "exp_r" (média em R)}.
    """
    h, l, c = k["h"], k["l"], k["c"]
    sgn = 1 if direcao == "LONG" else -1
    e20, e50 = ema(c, 20), ema(c, 50)
    atrs = atr_serie(h, l, c)
    resultados, i = [], BT_INICIO
    while i < len(c) - 1:
        a = atrs[i]
        regime = (1 if c[i] > e20[i] else -1) + (1 if e20[i] > e50[i] else -1)
        if not a or regime * sgn < 2:
            i += 1
            continue
        if tipo == "imediato":
            entrada = c[i]
            risco = STOP_ATR * a
            plano = Plano(sgn, entrada, entrada - sgn * risco,
                          entrada + sgn * ALVOS_R[0] * risco, entrada + sgn * ALVOS_R[1] * risco)
            inicio = i + 1
        else:
            entrada = min(e20[i], c[i] - ZONA_ATR * a) if sgn > 0 else max(e20[i], c[i] + ZONA_ATR * a)
            plano = Plano(sgn, entrada, entrada - sgn * STOP_COND_ATR * a,
                          entrada + sgn * ALVOS_COND_ATR[0] * a, entrada + sgn * ALVOS_COND_ATR[1] * a)
            inicio = next((j for j in range(i + 1, min(len(c), i + 1 + BT_GATILHO_CANDLES))
                           if (sgn > 0 and l[j] <= entrada) or (sgn < 0 and h[j] >= entrada)), None)
            if inicio is None:
                i += 1
                continue
        # o prazo só fecha a operação se ainda houver candle depois; sem ele, ela segue aberta
        saida = gerir(plano, h, l, c, inicio,
                      vencido=lambda j, ini=inicio: j - ini + 1 >= BT_PRAZO_CANDLES and j + 1 < len(c))
        if saida.situacao == "aberto":
            break  # ainda aberta no fim da série: não conta
        resultados.append(saida.r)
        i = saida.indice + 1
    if not resultados:
        return {"n": 0, "acerto": None, "exp_r": None}
    return {"n": len(resultados),
            "acerto": round(100 * sum(1 for r in resultados if r > 0) / len(resultados)),
            "exp_r": round(sum(resultados) / len(resultados), 2)}

# ---------------------------------------------------------------- análise de uma moeda


def analisar_seguro(ccy: str) -> Optional[dict]:
    """analisar(), mas um erro numa moeda vira falha dela, sem derrubar a varredura inteira."""
    try:
        return analisar(ccy)
    except Exception:
        print(f"[radar] {ccy}: erro na análise, moeda pulada\n{traceback.format_exc(limit=3)}", file=sys.stderr)
        return None


def analisar(ccy: str) -> dict:
    inst = f"{ccy}-USDT-SWAP"
    candles = {tf: baixar_candles(inst, tf, n) for tf, n in TEMPOS_GRAFICOS.items()}
    tfs = {tf: analisar_tempo_grafico(k) if k else None for tf, k in candles.items()}
    d = derivativos(ccy, inst)
    st = setup(tfs, d)
    imediato = st.get("entrada") is not None
    plano = st if imediato else st.get("plano_condicional")
    if plano and tfs.get("4H"):
        st["backtest"] = backtest(candles["4H"], plano["direcao"], "imediato" if imediato else "condicional")
    return {"timeframes": tfs, "derivativos": d, "setup": st}

# ---------------------------------------------------------------- nota de qualidade


def _limitar(x: float) -> float:
    return max(0.0, min(1.0, x))


def qualidade(linha: dict, bt: dict) -> int:
    """Nota de 0 a 100 da chance de a operação dar lucro. Pesos: backtest 50 (rendimento 35 +
    acerto 15), confluência 20, entrada não esticada 10, liquidez 10, posicionamento 10, +3 se imediato."""
    plano = linha["plano"]
    sgn = 1 if plano["direcao"] == "LONG" else -1
    n, exp, acerto = bt.get("n") or 0, bt.get("exp_r"), bt.get("acerto")
    peso = n / (n + 4)  # poucas operações no histórico valem menos
    nota = 0.0
    # backtest
    if exp is not None:
        nota += 35 * _limitar((exp * peso + 0.3) / 1.0)
        nota += 15 * _limitar(((acerto or 0) / 100 * peso + 0.5 * (1 - peso) - 0.3) / 0.4)
    else:
        nota += 35 * 0.3 + 15 * 0.3
    # confluência: pontuação e tempos gráficos a favor
    nota += 12 * min(1.0, abs(linha["pontuacao"] or 0) / 7)
    nota += 8 * sum(1 for k in ("t1d", "t4h", "t1h") if (linha.get(k) or 0) * sgn > 0) / 3
    # entrada: RSI 4H já andou mais de 10 pontos a favor perde nota, até zerar em 30
    andou = ((linha.get("rsi_4h") or 50) - 50) * sgn
    nota += 10 * _limitar(1 - max(0.0, andou - 10) / 20)
    # liquidez: de US$ 5 mi (0) a US$ 500 mi (1), em escala log
    nota += 10 * _limitar((math.log10(max(linha["vol_usd_m"], 0.01)) - math.log10(5)) / 2)
    # posicionamento: multidão do outro lado
    fr, ls = linha.get("funding"), linha.get("long_short")
    pos = 0.5
    if fr is not None:
        pos += 0.25 if fr * sgn < 0 else (-0.25 if fr * sgn > 0.02 else 0)
    if ls is not None:
        contra = (sgn > 0 and ls < 1) or (sgn < 0 and ls > 2)
        lotado = (sgn > 0 and ls > 3) or (sgn < 0 and ls < 0.8)
        pos += 0.25 if contra else (-0.25 if lotado else 0)
    nota += 10 * _limitar(pos)
    if plano["tipo"] == "imediato":
        nota += 3
    return int(round(max(0, min(100, nota))))


def backtest_negativo(bt: Optional[dict]) -> bool:
    bt = bt or {}
    return (bt.get("n") or 0) >= BT_MIN_OPERACOES and (bt.get("exp_r") or 0) <= 0


def marcar_top10(linhas: List[dict]) -> List[dict]:
    """Dá a nota a cada linha com plano e marca o Top 10. Devolve as elegíveis, melhores primeiro."""
    elegiveis = []
    for ln in linhas:
        if not ln["plano"]:
            continue
        ln["qualidade"] = qualidade(ln, ln["bt"] or {})
        if ln["vol_usd_m"] >= VOLUME_MIN_TOP_MI and not backtest_negativo(ln["bt"]):
            elegiveis.append(ln)
    elegiveis.sort(key=lambda x: (-x["qualidade"], -x["vol_usd_m"]))
    for pos, ln in enumerate(elegiveis[:TOP_N], 1):
        ln["top10"] = pos
    return elegiveis

# ---------------------------------------------------------------- radar de todas as moedas


def universo(min_vol: float) -> List[dict]:
    """Todas as criptos com perpétuo USDT ativo na OKX, com volume de 24h em dólares >= min_vol."""
    instrumentos = okx.get("/public/instruments?instType=SWAP") or []
    tickers = {t["instId"]: t for t in (okx.get("/market/tickers?instType=SWAP") or [])}
    out = []
    for x in instrumentos:
        iid = x.get("instId", "")
        cripto_ativo = (iid.endswith("-USDT-SWAP") and x.get("instCategory") == "1"
                        and x.get("ruleType") == "normal" and x.get("state") == "live")
        ccy = iid.split("-")[0]
        if not cripto_ativo or ccy in ESTAVEIS or iid not in tickers:
            continue
        t = tickers[iid]
        try:
            ultimo, abertura, vol_moeda = float(t["last"]), float(t["open24h"]), float(t["volCcy24h"])
        except (KeyError, ValueError):
            continue
        vol = vol_moeda * ultimo
        if vol >= min_vol:
            out.append({"ativo": ccy, "preco": ultimo,
                        "var_24h": rd_sig((ultimo / abertura - 1) * 100) if abertura else None,
                        "vol_usd_m": rd_sig(vol / 1e6)})
    return sorted(out, key=lambda r: -r["vol_usd_m"])


def plano_da_linha(st: dict) -> Optional[dict]:
    """O plano do setup no formato da tabela do radar (imediato ou condicional)."""
    niveis = ("entrada", "stop", "alvo1", "alvo2")
    if st.get("entrada") is not None:
        return {"direcao": st["direcao"], "tipo": "imediato", **{k: st[k] for k in niveis}}
    pc = st.get("plano_condicional")
    if pc:
        return {"direcao": pc["direcao"], "tipo": "condicional", **{k: pc[k] for k in niveis}}
    return None


def linha_radar(base: dict, a: dict) -> dict:
    """Uma linha compacta da tabela do radar (o que o painel mostra para cada moeda)."""
    tf, d, st = a["timeframes"], a["derivativos"], a["setup"]
    d1, h4, h1 = tf.get("1D"), tf.get("4H"), tf.get("1H")
    return dict(base, **{
        "t1d": d1 and d1["tendencia"], "t4h": h4 and h4["tendencia"], "t1h": h1 and h1["tendencia"],
        "rsi_1d": d1 and d1["rsi14"], "rsi_4h": h4 and h4["rsi14"],
        "ema200_1d": d1 and d1["ema200"],
        "suporte": h4 and (h4["suportes"] or [None])[0], "resistencia": h4 and (h4["resistencias"] or [None])[0],
        "funding": d.get("funding_8h_pct"), "oi_7d": d.get("oi_var_7d_pct"), "long_short": d.get("long_short_contas"),
        "pontuacao": st.get("pontuacao"), "setup": st.get("direcao"), "motivos": st.get("motivos", []),
        "plano": plano_da_linha(st), "bt": st.get("backtest"), "qualidade": None, "top10": None,
    })


def varrer(min_vol: float, threads: int):
    """Analisa todo o universo. Devolve (completo, linhas do radar, moedas que falharam)."""
    uni = universo(min_vol)
    if not uni:
        return None, [], []
    with ThreadPoolExecutor(max_workers=threads) as ex:
        analises = list(ex.map(lambda b: (b, analisar_seguro(b["ativo"])), uni))
    completo = {"gerado_em": agora_iso(), "fonte": "OKX perpétuos USDT (todas as criptos com volume)",
                "min_vol_usd": min_vol, "ativos": {}}
    linhas, falhas = [], []
    for base, a in analises:
        if a is None or not all(a["timeframes"].get(tf) for tf in TEMPOS_GRAFICOS):
            falhas.append(base["ativo"])
            continue
        completo["ativos"][base["ativo"]] = dict(a, ticker=base)
        linhas.append(linha_radar(base, a))
    return completo, linhas, falhas


def montar_resumo(completo: dict, linhas: List[dict], falhas: List[str], elegiveis: List[dict],
                  min_vol: float, top: int) -> dict:
    """O que a rodada lê: contagens, o Top 10 com o detalhe completo e as melhores de fora."""
    com_plano = [l for l in linhas if l["plano"]]
    fora = sorted(com_plano, key=lambda x: -(x["qualidade"] or 0))
    return {
        "gerado_em": completo["gerado_em"], "moedas_analisadas": len(linhas), "falhas": falhas, "min_vol_usd": min_vol,
        "com_setup": len(com_plano),
        "vies_alta": sum(1 for l in linhas if (l["pontuacao"] or 0) >= PONTUACAO_PLANO),
        "vies_baixa": sum(1 for l in linhas if (l["pontuacao"] or 0) <= -PONTUACAO_PLANO),
        "regra_top10": "nota de qualidade 0-100 (backtest 4H do mesmo plano na moeda, confluência, RSI, liquidez, "
                       "posicionamento); só moedas com plano, volume >= US$ 5 mi e sem backtest negativo",
        "top10": [dict(l, detalhe=completo["ativos"][l["ativo"]]) for l in elegiveis[:TOP_N]],
        "fora_do_top10": [{"ativo": l["ativo"], "qualidade": l["qualidade"],
                           "plano": l["plano"]["direcao"] + " " + l["plano"]["tipo"], "bt": l["bt"]}
                          for l in fora if not l["top10"]][:top],
    }


def salvar_json(caminho: str, obj) -> None:
    with open(caminho, "w") as f:
        json.dump(obj, f, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ativos", nargs="*", help="moedas para analisar (modo simples)")
    ap.add_argument("--todas", action="store_true", help="varre todas as criptos com perpétuo USDT")
    ap.add_argument("--min-vol", type=float, default=1_000_000, help="volume mínimo em 24h, em dólares")
    ap.add_argument("--saida", help="arquivo para o JSON completo")
    ap.add_argument("--scanner", help="arquivo para a tabela do radar (formato do painel)")
    ap.add_argument("--top", type=int, default=12, help="quantas moedas de fora do Top 10 listar no resumo")
    ap.add_argument("--threads", type=int, default=6)
    args = ap.parse_args()

    if not args.todas:
        ativos = [a.upper() for a in args.ativos] or ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]
        res = {"gerado_em": agora_iso(), "fonte": "OKX perpétuos USDT", "ativos": {ccy: analisar_seguro(ccy) for ccy in ativos}}
        json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
        return

    completo, linhas, falhas = varrer(args.min_vol, args.threads)
    if completo is None:
        print(json.dumps({"erro": "não consegui listar as moedas da OKX"}, ensure_ascii=False))
        sys.exit(1)
    elegiveis = marcar_top10(linhas)
    if args.saida:
        salvar_json(args.saida, completo)
    if args.scanner:
        salvar_json(args.scanner, {"at": completo["gerado_em"], "min_vol_usd": args.min_vol, "total": len(linhas),
                                   "falhas": falhas, "moedas_json": json_compacto(linhas)})
    resumo = montar_resumo(completo, linhas, falhas, elegiveis, args.min_vol, args.top)
    json.dump(resumo, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
