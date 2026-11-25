#!/usr/bin/env python3
"""Análise técnica multi-timeframe de futuros perpétuos (dados públicos da OKX).

Uso:
  python3 analise_tecnica.py BTC ETH SOL
      analisa só as moedas pedidas e imprime o JSON completo.
  python3 analise_tecnica.py --todas [--min-vol 1000000] [--saida ta.json] [--scanner scanner.json] [--top 12]
      varre TODAS as criptos com perpétuo USDT na OKX (instCategory 1, sem ações nem commodities,
      sem stablecoins) com volume de 24h acima de --min-vol dólares. Grava o JSON completo em --saida,
      a tabela compacta do radar em --scanner e imprime um resumo com os melhores candidatos.
Só biblioteca padrão. Nada aqui é recomendação de investimento.
"""
import argparse, json, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://www.okx.com/api/v5"
TFS = {"1D": 300, "4H": 300, "1H": 300}
ESTAVEIS = {"USDC", "USDT", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USDD", "BUSD", "USD1", "RLUSD", "USDG"}

# limites da OKX por IP (com folga): candles 40/2s, funding 20/2s, rubik 5/2s
_LIMITES = {"/market/": 12.0, "/public/": 6.0, "/rubik/": 2.0}
_proxima = {k: 0.0 for k in _LIMITES}
_trava = threading.Lock()


def _esperar(path):
    grupo = next((g for g in _LIMITES if path.startswith(g)), None)
    if not grupo:
        return
    with _trava:
        agora = time.monotonic()
        quando = max(agora, _proxima[grupo])
        _proxima[grupo] = quando + 1.0 / _LIMITES[grupo]
    if quando > agora:
        time.sleep(quando - agora)


def get(path):
    for i in range(3):
        _esperar(path)
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": "Mozilla/5.0 radar-analise/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            if d.get("code") == "0":
                return d["data"]
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def rd(x):
    """Arredonda por algarismos significativos (serve para BTC e para DOGE)."""
    return None if x is None else float(f"{x:.6g}")


def ema(v, n):
    k, out = 2 / (n + 1), [v[0]]
    for x in v[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def rsi(c, n=14):
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]; g += max(d, 0); l += max(-d, 0)
    g, l = g / n, l / n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
    return 100 if l == 0 else 100 - 100 / (1 + g / l)


def atr(h, l, c, n=14):
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def swings(h, l, w=5, look=120):
    hs, ls = [], []
    s = max(w, len(h) - look)
    for i in range(s, len(h) - w):
        if h[i] == max(h[i - w:i + w + 1]): hs.append(h[i])
        if l[i] == min(l[i - w:i + w + 1]): ls.append(l[i])
    return hs, ls


def tf_analysis(inst, bar, limit):
    rows = get(f"/market/candles?instId={inst}&bar={bar}&limit={min(limit, 300)}")
    if not rows:
        return None
    seen, data = set(), []
    for r in rows:
        if r[0] not in seen and r[8] == "1":  # só candles fechados
            seen.add(r[0]); data.append(r)
    data.sort(key=lambda r: int(r[0]))
    o, h, l, c, v = ([float(r[i]) for r in data] for i in (1, 2, 3, 4, 5))
    if len(c) < 60:
        return None
    e20, e50 = ema(c, 20), ema(c, 50)
    e200 = ema(c, 200) if len(c) >= 200 else None
    m12, m26 = ema(c, 12), ema(c, 26)
    macd = [a - b for a, b in zip(m12, m26)]
    sig = ema(macd, 9)
    last = c[-1]
    mean20 = sum(c[-20:]) / 20
    sd = (sum((x - mean20) ** 2 for x in c[-20:]) / 20) ** 0.5
    hs, ls = swings(h, l)
    res = sorted({rd(x) for x in hs if x > last})[:3]
    sup = sorted({rd(x) for x in ls if x < last}, reverse=True)[:3]
    trend = 0
    trend += 1 if last > e20[-1] else -1
    trend += 1 if e20[-1] > e50[-1] else -1
    if e200: trend += 1 if last > e200[-1] else -1
    vol_ratio = (sum(v[-5:]) / 5) / (sum(v[-50:]) / 50)
    return {
        "fechamento": last, "ema20": rd(e20[-1]), "ema50": rd(e50[-1]),
        "ema200": rd(e200[-1]) if e200 else None,
        "rsi14": round(rsi(c), 1), "macd_hist": rd(macd[-1] - sig[-1]),
        "macd_hist_ant": rd(macd[-2] - sig[-2]),
        "atr14": rd(atr(h, l, c)),
        "bollinger": [rd(mean20 - 2 * sd), rd(mean20 + 2 * sd)],
        "resistencias": res, "suportes": sup,
        "max_20": max(h[-20:]), "min_20": min(l[-20:]),
        "volume_vs_media50": rd(vol_ratio),
        "variacao_10_candles_pct": rd((last / c[-11] - 1) * 100),
        "tendencia": trend,  # -3 (baixa forte) a +3 (alta forte)
    }


def derivativos(ccy, inst):
    out = {}
    f = get(f"/public/funding-rate?instId={inst}")
    if f: out["funding_8h_pct"] = round(float(f[0]["fundingRate"]) * 100, 4)
    oi = get(f"/rubik/stat/contracts/open-interest-volume?ccy={ccy}&period=1D")
    if oi and len(oi) > 7:
        out["oi_usd"] = float(oi[0][1]); out["oi_var_7d_pct"] = round((float(oi[0][1]) / float(oi[7][1]) - 1) * 100, 2)
    ls = get(f"/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=1H")
    if ls: out["long_short_contas"] = float(ls[0][1])
    return out


def setup(a, d):
    t1d, t4h, t1h = (a.get(k) for k in ("1D", "4H", "1H"))
    if not (t1d and t4h and t1h):
        return {"direcao": "SEM SINAL", "motivo": "dados insuficientes"}
    score, why = 0, []
    score += t1d["tendencia"]; why.append(f"tendência 1D {t1d['tendencia']:+d}")
    score += t4h["tendencia"]; why.append(f"tendência 4H {t4h['tendencia']:+d}")
    if t4h["macd_hist"] > 0 and t4h["macd_hist"] > t4h["macd_hist_ant"]: score += 1; why.append("MACD 4H virando para cima")
    if t4h["macd_hist"] < 0 and t4h["macd_hist"] < t4h["macd_hist_ant"]: score -= 1; why.append("MACD 4H ganhando força para baixo")
    if t1h["tendencia"] > 0: score += 1
    if t1h["tendencia"] < 0: score -= 1
    fr = d.get("funding_8h_pct")
    if fr is not None:
        if fr > 0.03: score -= 1; why.append("funding alto: comprados lotados")
        if fr < -0.01: score += 1; why.append("funding negativo: vendidos lotados (risco de short squeeze)")
    r4 = t4h["rsi14"]
    direcao = "LONG" if score >= 5 else "SHORT" if score <= -5 else "SEM SINAL"
    if direcao == "LONG" and r4 > 72: direcao, _ = "SEM SINAL", why.append("RSI 4H sobrecomprado: esperar recuo")
    if direcao == "SHORT" and r4 < 28: direcao, _ = "SEM SINAL", why.append("RSI 4H sobrevendido: esperar repique")
    if direcao == "SHORT" and t1d["rsi14"] < 25: direcao, _ = "SEM SINAL", why.append(f"RSI diário {t1d['rsi14']} (sobrevenda extrema): vender aqui é correr atrás do preço")
    if direcao == "LONG" and t1d["rsi14"] > 75: direcao, _ = "SEM SINAL", why.append(f"RSI diário {t1d['rsi14']} (sobrecompra extrema)")
    px, at = t4h["fechamento"], t4h["atr14"]
    out = {"direcao": direcao, "pontuacao": score, "motivos": why, "preco": px}
    if direcao == "SEM SINAL":
        # plano condicional: operar a favor da tendência só depois de um repique/recuo até a EMA20 do 4H
        if score <= -3:
            z = max(t4h["ema20"], px + 0.5 * t4h["atr14"])
            out["plano_condicional"] = {"direcao": "SHORT", "gatilho": f"repique até a zona de {rd(z)} com rejeição (candle 4H fechando abaixo)",
                                        "entrada": rd(z), "stop": rd(z + 1.5 * t4h["atr14"]), "alvo1": rd(z - 2.25 * t4h["atr14"]), "alvo2": rd(z - 4.5 * t4h["atr14"])}
        elif score >= 3:
            z = min(t4h["ema20"], px - 0.5 * t4h["atr14"])
            out["plano_condicional"] = {"direcao": "LONG", "gatilho": f"recuo até a zona de {rd(z)} com defesa (candle 4H fechando acima)",
                                        "entrada": rd(z), "stop": rd(z - 1.5 * t4h["atr14"]), "alvo1": rd(z + 2.25 * t4h["atr14"]), "alvo2": rd(z + 4.5 * t4h["atr14"])}
        return out
    sgn = 1 if direcao == "LONG" else -1
    stop = px - sgn * 1.5 * at
    lvl = t4h["suportes"] if sgn > 0 else t4h["resistencias"]
    if lvl:  # stop além do suporte/resistência mais próximo, se estiver perto
        alt = lvl[0] - sgn * 0.3 * at
        if 1.0 * at <= abs(px - alt) < 2.5 * at and (alt - px) * sgn < 0: stop = alt
    risk = abs(px - stop)
    out.update({"entrada": rd(px), "stop": rd(stop),
                "alvo1": rd(px + sgn * 1.5 * risk), "alvo2": rd(px + sgn * 3 * risk),
                "risco_pct": rd(risk / px * 100),
                # alavancagem para perder no máximo ~2% da margem se o stop for atingido, limitada a 5x
                "alavancagem_max": max(1, min(5, int(2 / max(risk / px * 100, 0.01))))})
    return out


def analisar(ccy):
    inst = f"{ccy}-USDT-SWAP"
    tfs = {tf: tf_analysis(inst, tf, n) for tf, n in TFS.items()}
    d = derivativos(ccy, inst)
    return {"timeframes": tfs, "derivativos": d, "setup": setup(tfs, d)}


def universo(min_vol):
    """Todas as criptos com perpétuo USDT ativo na OKX, com volume de 24h em dólares >= min_vol."""
    inst = get("/public/instruments?instType=SWAP") or []
    tick = {t["instId"]: t for t in (get("/market/tickers?instType=SWAP") or [])}
    out = []
    for x in inst:
        iid = x.get("instId", "")
        if not iid.endswith("-USDT-SWAP") or x.get("instCategory") != "1" or x.get("ruleType") != "normal" or x.get("state") != "live":
            continue
        ccy = iid.split("-")[0]
        if ccy in ESTAVEIS or iid not in tick:
            continue
        t = tick[iid]
        try:
            last, open24, volccy = float(t["last"]), float(t["open24h"]), float(t["volCcy24h"])
        except (KeyError, ValueError):
            continue
        vol = volccy * last
        if vol >= min_vol:
            out.append({"ativo": ccy, "preco": last, "var_24h": rd((last / open24 - 1) * 100) if open24 else None, "vol_usd_m": rd(vol / 1e6)})
    return sorted(out, key=lambda r: -r["vol_usd_m"])


def linha_radar(base, a):
    """Uma linha compacta da tabela do radar (o que o painel mostra para cada moeda)."""
    tf, d, st = a["timeframes"], a["derivativos"], a["setup"]
    d1, h4, h1 = tf.get("1D"), tf.get("4H"), tf.get("1H")
    plano = None
    if st.get("entrada") is not None:
        plano = {"direcao": st["direcao"], "tipo": "imediato", "entrada": st["entrada"], "stop": st["stop"], "alvo1": st["alvo1"], "alvo2": st["alvo2"]}
    elif st.get("plano_condicional"):
        pc = st["plano_condicional"]
        plano = {"direcao": pc["direcao"], "tipo": "condicional", "entrada": pc["entrada"], "stop": pc["stop"], "alvo1": pc["alvo1"], "alvo2": pc["alvo2"]}
    return dict(base, **{
        "t1d": d1 and d1["tendencia"], "t4h": h4 and h4["tendencia"], "t1h": h1 and h1["tendencia"],
        "rsi_1d": d1 and d1["rsi14"], "rsi_4h": h4 and h4["rsi14"],
        "ema200_1d": d1 and d1["ema200"], "suporte": h4 and (h4["suportes"] or [None])[0], "resistencia": h4 and (h4["resistencias"] or [None])[0],
        "funding": d.get("funding_8h_pct"), "oi_7d": d.get("oi_var_7d_pct"), "long_short": d.get("long_short_contas"),
        "pontuacao": st.get("pontuacao"), "setup": st.get("direcao"), "motivos": st.get("motivos", []), "plano": plano,
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ativos", nargs="*")
    ap.add_argument("--todas", action="store_true")
    ap.add_argument("--min-vol", type=float, default=1_000_000)
    ap.add_argument("--saida")
    ap.add_argument("--scanner")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--threads", type=int, default=6)
    args = ap.parse_args()
    agora = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if not args.todas:
        ativos = [a.upper() for a in args.ativos] or ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]
        res = {"gerado_em": agora, "fonte": "OKX perpétuos USDT", "ativos": {}}
        for ccy in ativos:
            res["ativos"][ccy] = analisar(ccy)
        json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
        return

    uni = universo(args.min_vol)
    if not uni:
        print(json.dumps({"erro": "não consegui listar as moedas da OKX"}, ensure_ascii=False)); sys.exit(1)
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        res_list = list(ex.map(lambda b: (b, analisar(b["ativo"])), uni))
    completo = {"gerado_em": agora, "fonte": "OKX perpétuos USDT (todas as criptos com volume)", "min_vol_usd": args.min_vol, "ativos": {}}
    linhas, falhas = [], []
    for base, a in res_list:
        if not (a["timeframes"].get("1D") and a["timeframes"].get("4H") and a["timeframes"].get("1H")):
            falhas.append(base["ativo"]); continue
        completo["ativos"][base["ativo"]] = dict(a, ticker=base)
        linhas.append(linha_radar(base, a))
    if args.saida:
        json.dump(completo, open(args.saida, "w"), ensure_ascii=False)
    if args.scanner:
        doc = {"at": agora, "min_vol_usd": args.min_vol, "total": len(linhas), "falhas": falhas,
               "moedas_json": json.dumps(linhas, ensure_ascii=False, separators=(",", ":"))}
        json.dump(doc, open(args.scanner, "w"), ensure_ascii=False)
    # candidatos: moedas com setup (imediato ou condicional), mais fortes primeiro
    cand = [l for l in linhas if l["plano"]]
    cand.sort(key=lambda l: (-(l["plano"]["tipo"] == "imediato"), -abs(l["pontuacao"] or 0), -l["vol_usd_m"]))
    resumo = {
        "gerado_em": agora, "moedas_analisadas": len(linhas), "falhas": falhas, "min_vol_usd": args.min_vol,
        "com_setup": len(cand), "vies_alta": sum(1 for l in linhas if (l["pontuacao"] or 0) >= 3),
        "vies_baixa": sum(1 for l in linhas if (l["pontuacao"] or 0) <= -3),
        "candidatos": [dict(l, detalhe=completo["ativos"][l["ativo"]]) for l in cand[:args.top]],
    }
    json.dump(resumo, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
