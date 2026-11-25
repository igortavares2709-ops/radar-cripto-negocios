"""Testes das regras do radar, sem rede. Rode na raiz do repositório:

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import analise_tecnica as at  # noqa: E402
import avaliar_sinais as av  # noqa: E402
import carteira as ct  # noqa: E402
from indicadores import atr, atr_serie, ema, rsi, swings  # noqa: E402
from radar_comum import Plano, carregar_sinais, gerir, iso, ms, rd2, rd_sig  # noqa: E402

HORA = 3600_000
T0 = ms("2026-11-20T00:00:00Z")


def nunca(_):
    return False


class TestIndicadores(unittest.TestCase):
    def test_ema_de_serie_constante(self):
        self.assertEqual(ema([5.0] * 30, 10), [5.0] * 30)

    def test_rsi_extremos(self):
        self.assertEqual(rsi([float(i) for i in range(30)]), 100)
        self.assertLess(rsi([float(30 - i) for i in range(30)]), 1)

    def test_atr_de_candles_iguais(self):
        h, l, c = [11.0] * 30, [9.0] * 30, [10.0] * 30
        self.assertAlmostEqual(atr(h, l, c), 2.0)
        serie = atr_serie(h, l, c)
        self.assertIsNone(serie[13])
        self.assertAlmostEqual(serie[14], 2.0)
        self.assertEqual(serie[-1], atr(h, l, c))

    def test_swings_acha_topo_e_fundo(self):
        h = [1.0] * 10 + [5.0] + [1.0] * 10
        l = [1.0] * 10 + [0.5] + [1.0] * 10
        topos, fundos = swings(h, l)
        self.assertIn(5.0, topos)
        self.assertIn(0.5, fundos)


class TestArredondamento(unittest.TestCase):
    def test_rd_sig_serve_para_preco_grande_e_pequeno(self):
        self.assertEqual(rd_sig(65417.123456), 65417.1)
        self.assertEqual(rd_sig(0.000012345678), 0.0000123457)
        self.assertIsNone(rd_sig(None))

    def test_datas_ida_e_volta(self):
        self.assertEqual(iso(ms("2026-11-25T15:00:00Z")), "2026-11-25T15:00:00Z")


class TestGerir(unittest.TestCase):
    """Plano LONG: entrada 100, stop 90 (1R = 10), alvo1 115 (1,5R), alvo2 130 (3R)."""
    p = Plano(1, 100.0, 90.0, 115.0, 130.0)

    def rodar(self, candles, vencido=nunca):
        h, l, c = zip(*candles)
        return gerir(self.p, h, l, c, 0, vencido)

    def test_stop(self):
        s = self.rodar([(105, 95, 100), (101, 89, 92)])
        self.assertEqual((s.situacao, s.r, s.indice), ("stop", -1.0, 1))

    def test_stop_e_alvo_no_mesmo_candle_conta_stop(self):
        self.assertEqual(self.rodar([(116, 89, 100)]).situacao, "stop")

    def test_alvo1_e_volta_na_entrada(self):
        s = self.rodar([(116, 101, 110), (112, 99, 100)])
        self.assertEqual(s.situacao, "alvo1")
        self.assertAlmostEqual(s.r, 0.75)

    def test_alvo2(self):
        s = self.rodar([(116, 101, 110), (131, 110, 129)])
        self.assertEqual(s.situacao, "alvo2")
        self.assertAlmostEqual(s.r, 2.25)

    def test_alvo1_e_alvo2_no_mesmo_candle(self):
        self.assertEqual(self.rodar([(131, 101, 125)]).situacao, "alvo2")

    def test_stop_vai_para_a_entrada_so_no_candle_seguinte(self):
        # no candle do alvo 1 a mínima foi a 99 (abaixo da entrada), mas o stop ainda era 90
        s = self.rodar([(116, 99, 110), (120, 105, 118)])
        self.assertEqual(s.situacao, "aberto")
        self.assertTrue(s.meia)

    def test_prazo_sai_pelo_fechamento(self):
        s = self.rodar([(105, 95, 102), (106, 96, 104)], vencido=lambda j: j == 1)
        self.assertEqual(s.situacao, "prazo")
        self.assertAlmostEqual(s.r, 0.4)

    def test_aberto_tem_resultado_flutuante(self):
        s = self.rodar([(105, 95, 103)])
        self.assertEqual(s.situacao, "aberto")
        self.assertAlmostEqual(s.r, 0.3)

    def test_short_espelhado(self):
        p = Plano(-1, 100.0, 110.0, 85.0, 70.0)
        h, l, c = (101, 84), (95, 69), (98, 72)
        s = gerir(p, h, l, c, 0, nunca)
        self.assertEqual(s.situacao, "alvo2")
        self.assertAlmostEqual(s.r, 2.25)


def sinal(**kw):
    base = {"id": "f1", "at": "2026-11-20T00:00:00Z", "ativo": "BTC", "direcao": "LONG", "tipo": "imediato",
            "entrada": 100, "stop": 90, "alvo1": 115, "alvo2": 130}
    base.update(kw)
    return base


def candles(*hlc, t0=T0):
    return {"t": [t0 + i * HORA for i in range(len(hlc))], "h": [x[0] for x in hlc],
            "l": [x[1] for x in hlc], "c": [x[2] for x in hlc]}


class TestAvaliarSinais(unittest.TestCase):
    def test_imediato_no_stop(self):
        r = av.avaliar(sinal(), candles((105, 95, 100), (101, 89, 92)), T0 + 5 * HORA)
        self.assertEqual(r["status"], "stop")
        self.assertEqual(r["resultado_r"], -1.0)
        self.assertEqual(r["encerrado_em"], iso(T0 + HORA))

    def test_candle_anterior_ao_sinal_nao_conta(self):
        k = candles((101, 80, 92), (105, 95, 100), t0=T0 - HORA)  # o stop foi tocado antes do sinal
        self.assertEqual(av.avaliar(sinal(), k, T0 + 2 * HORA)["status"], "aberto")

    def test_condicional_expira_em_48h(self):
        s = sinal(tipo="condicional", entrada=95, stop=85, alvo1=110, alvo2=125)
        k = candles(*[(105, 100, 102)] * 50)
        r = av.avaliar(s, k, T0 + 50 * HORA)
        self.assertEqual((r["status"], r["resultado_r"]), ("expirado", 0))

    def test_condicional_aguardando_antes_de_48h(self):
        s = sinal(tipo="condicional", entrada=95, stop=85, alvo1=110, alvo2=125)
        self.assertEqual(av.avaliar(s, candles((105, 100, 102)), T0 + 2 * HORA)["status"], "aguardando")

    def test_condicional_aciona_no_toque(self):
        s = sinal(tipo="condicional", entrada=95, stop=85, alvo1=110, alvo2=125)
        r = av.avaliar(s, candles((105, 100, 102), (99, 94, 96), (126, 96, 124)), T0 + 3 * HORA)
        self.assertEqual(r["status"], "alvo2")
        self.assertEqual(r["acionado_em"], iso(T0 + HORA))

    def test_prazo_de_7_dias(self):
        k = candles(*[(105, 95, 101)] * 200)
        r = av.avaliar(sinal(), k, T0 + 200 * HORA)
        self.assertEqual(r["status"], "encerrado no prazo")
        self.assertEqual(r["encerrado_em"], iso(T0 + 169 * HORA))
        self.assertAlmostEqual(r["resultado_r"], 0.1)

    def test_stop_igual_entrada_e_invalido(self):
        self.assertEqual(av.avaliar(sinal(stop=100), candles((1, 1, 1)), T0)["status"], "invalido")

    def test_encerrados_nao_sao_reavaliados(self):
        self.assertEqual(av.avaliar_todos([sinal(status="stop")], T0), {})


class TestSetup(unittest.TestCase):
    def tf(self, tendencia, rsi14=50.0, macd=(0.0, 0.0)):
        return {"tendencia": tendencia, "rsi14": rsi14, "macd_hist": macd[0], "macd_hist_ant": macd[1],
                "fechamento": 100.0, "atr14": 2.0, "ema20": 98.0, "suportes": [], "resistencias": []}

    def test_long_imediato_com_confluencia(self):
        st = at.setup({"1D": self.tf(3), "4H": self.tf(3, macd=(1.0, 0.5)), "1H": self.tf(1)}, {})
        self.assertEqual(st["direcao"], "LONG")
        self.assertEqual((st["entrada"], st["stop"], st["alvo1"], st["alvo2"]), (100.0, 97.0, 104.5, 109.0))

    def test_sobrecompra_vira_plano_condicional(self):
        st = at.setup({"1D": self.tf(3), "4H": self.tf(3, rsi14=80), "1H": self.tf(1)}, {})
        self.assertEqual(st["direcao"], "SEM SINAL")
        self.assertIn("RSI 4H sobrecomprado: esperar recuo", st["motivos"])
        self.assertEqual(st["plano_condicional"]["direcao"], "LONG")
        self.assertEqual(st["plano_condicional"]["entrada"], 98.0)

    def test_sem_direcao_sem_plano(self):
        st = at.setup({"1D": self.tf(1), "4H": self.tf(-1), "1H": self.tf(0)}, {})
        self.assertEqual(st["direcao"], "SEM SINAL")
        self.assertNotIn("plano_condicional", st)

    def test_dados_insuficientes(self):
        self.assertEqual(at.setup({"1D": None, "4H": None, "1H": None}, {})["motivo"], "dados insuficientes")


class TestBacktestETop10(unittest.TestCase):
    def test_tendencia_de_alta_limpa_da_lucro_no_long(self):
        c = [100 * 1.004 ** i for i in range(300)]
        k = {"h": [x * 1.01 for x in c], "l": [x * 0.99 for x in c], "c": c}
        bt = at.backtest(k, "LONG", "imediato")
        self.assertGreater(bt["n"], 0)
        self.assertGreater(bt["exp_r"], 0)
        self.assertEqual(at.backtest(k, "SHORT", "imediato")["n"], 0)

    def linha(self, ativo, vol, bt, pontuacao=-6):
        return {"ativo": ativo, "vol_usd_m": vol, "pontuacao": pontuacao, "t1d": -3, "t4h": -3, "t1h": -1,
                "rsi_4h": 40, "funding": 0.01, "long_short": 2.5, "bt": bt,
                "plano": {"direcao": "SHORT", "tipo": "imediato"}, "qualidade": None, "top10": None}

    def test_qualidade_fica_entre_0_e_100(self):
        for bt in ({}, {"n": 50, "acerto": 100, "exp_r": 5}, {"n": 50, "acerto": 0, "exp_r": -1}):
            q = at.qualidade(self.linha("X", 1000, bt), bt)
            self.assertTrue(0 <= q <= 100)

    def test_top10_exclui_pouco_volume_e_backtest_negativo(self):
        bom = {"n": 8, "acerto": 75, "exp_r": 1.0}
        linhas = [self.linha("FINA", 2, bom), self.linha("RUIM", 100, {"n": 5, "acerto": 20, "exp_r": -0.3})]
        linhas += [self.linha(f"M{i}", 10 + i, bom) for i in range(12)]
        linhas.append(dict(self.linha("SEMPLANO", 100, None), plano=None))
        elegiveis = at.marcar_top10(linhas)
        nomes = [l["ativo"] for l in elegiveis]
        self.assertNotIn("FINA", nomes)
        self.assertNotIn("RUIM", nomes)
        self.assertEqual(sum(1 for l in linhas if l["top10"]), 10)
        self.assertIsNone(linhas[-1]["qualidade"])
        self.assertEqual(elegiveis[0]["top10"], 1)


class TestCarteira(unittest.TestCase):
    def op(self, id_, ini, fim, r, status="alvo2", entrada=100, stop=90, **kw):
        s = sinal(id=id_, entrada=entrada, stop=stop, status=status, acionado_em=ini, resultado_r=r)
        if fim:
            s["encerrado_em"] = fim
        s.update(kw)
        return s

    def test_saldo_com_risco_de_1pct_e_taxas(self):
        sinais = [self.op("a", "2026-11-20T01:00:00Z", "2026-11-20T05:00:00Z", 2.25),
                  self.op("b", "2026-11-21T01:00:00Z", "2026-11-21T05:00:00Z", -1.0, status="stop")]
        doc = ct.carteira(sinais, ct.Regras())
        # a: arrisca 20, posição 200 (stop a 10%), taxas 0,20 -> +44,80
        # b: arrisca 20,448, posição 204,48, taxas 0,20448 -> -20,65248
        self.assertAlmostEqual(doc["saldo"], rd2(2000 + 44.8 - 20.448 - 0.20448))
        self.assertEqual((doc["operacoes"], doc["ganhos"], doc["perdas"]), (2, 1, 1))
        self.assertEqual(doc["acerto_pct"], 50.0)

    def test_posicao_limitada_pela_alavancagem(self):
        # stop a 0,1% da entrada: 1% de risco pediria posição de 10x o saldo; o limite é 5x
        sinais = [self.op("a", "2026-11-20T01:00:00Z", "2026-11-20T05:00:00Z", -1.0, status="stop", stop=99.9)]
        extrato = json.loads(ct.carteira(sinais, ct.Regras())["extrato_json"])
        self.assertAlmostEqual(extrato[0]["tamanho_usd"], 10000.0)
        self.assertAlmostEqual(extrato[0]["risco_usd"], 10.0, places=6)

    def test_expirado_e_nao_acionado_ficam_de_fora(self):
        sinais = [sinal(id="x", status="expirado", resultado_r=0), sinal(id="y", status="aguardando")]
        doc = ct.carteira(sinais, ct.Regras())
        self.assertEqual((doc["saldo"], doc["operacoes"], doc["abertas"]), (2000.0, 0, 0))

    def test_aberta_nao_mexe_no_saldo(self):
        sinais = [self.op("a", "2026-11-20T01:00:00Z", None, None, status="aberto", r_flutuante=0.5)]
        doc = ct.carteira(sinais, ct.Regras())
        self.assertEqual(doc["saldo"], 2000.0)
        self.assertEqual(doc["abertas"], 1)
        self.assertAlmostEqual(doc["flutuante_usd"], 10 - 0.1)

    def test_saida_antes_da_entrada_no_mesmo_horario(self):
        # "b" entra na mesma hora em que "a" sai: o risco de "b" usa o saldo já com o lucro de "a"
        sinais = [self.op("a", "2026-11-20T01:00:00Z", "2026-11-20T05:00:00Z", 2.25),
                  self.op("b", "2026-11-20T05:00:00Z", "2026-11-20T09:00:00Z", -1.0, status="stop")]
        extrato = json.loads(ct.carteira(sinais, ct.Regras())["extrato_json"])
        self.assertAlmostEqual(extrato[1]["risco_usd"], rd2(2044.8 * 0.01))


class TestCarregarSinais(unittest.TestCase):
    def test_aceita_formato_do_artifactdata(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"documents": [{"id": "f1", "data": {"ativo": "BTC"}}]}, f)
        try:
            self.assertEqual(carregar_sinais(f.name), [{"ativo": "BTC", "id": "f1"}])
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()


class TestRobustez(unittest.TestCase):
    """Dados ruins numa moeda ou num sinal não podem derrubar a rodada inteira."""

    def test_volume_zero_nao_quebra(self):
        k = {"o": [1.0] * 80, "h": [1.1] * 80, "l": [0.9] * 80, "c": [1.0] * 80, "v": [0.0] * 80}
        self.assertIsNone(at.analisar_tempo_grafico(k)["volume_vs_media50"])

    def test_atr_zero_nao_gera_plano(self):
        tf = {"tendencia": 3, "rsi14": 50.0, "macd_hist": 1.0, "macd_hist_ant": 0.5, "fechamento": 100.0,
              "atr14": 0.0, "ema20": 100.0, "suportes": [], "resistencias": []}
        st = at.setup({"1D": tf, "4H": tf, "1H": tf}, {})
        self.assertEqual(st["direcao"], "SEM SINAL")
        self.assertNotIn("entrada", st)
        self.assertNotIn("plano_condicional", st)

    def test_open_interest_zerado_so_some_o_campo(self):
        orig = at.okx.get
        at.okx.get = lambda p: [["0", "0"]] * 8 if "open-interest" in p else [{"fundingRate": ""}] if "funding" in p else None
        try:
            d = at.derivativos("X", "X-USDT-SWAP")
        finally:
            at.okx.get = orig
        self.assertEqual(d, {"oi_usd": 0.0})

    def test_moeda_quebrada_nao_derruba_a_varredura(self):
        orig_uni, orig_an = at.universo, at.analisar
        at.universo = lambda mv: [{"ativo": "RUIM", "preco": 1, "var_24h": 0, "vol_usd_m": 9}]
        at.analisar = lambda ccy: 1 / 0
        try:
            completo, linhas, falhas = at.varrer(1, 2)
        finally:
            at.universo, at.analisar = orig_uni, orig_an
        self.assertEqual((linhas, falhas), ([], ["RUIM"]))

    def test_sinal_ruim_nao_derruba_a_conferencia(self):
        orig = av.candles_1h
        av.candles_1h = lambda ccy, desde=None: candles((105, 95, 100), (101, 89, 92))
        try:
            out = av.avaliar_todos([sinal(id="ruim", at="data ruim"), sinal(id="bom"),
                                    sinal(id="virgula", entrada="100,0")], T0 + 5 * HORA)
        finally:
            av.candles_1h = orig
        self.assertIn("dados inválidos", out["ruim"]["nota"])
        self.assertEqual(out["bom"]["status"], "stop")
        self.assertEqual(out["virgula"]["status"], "stop")  # vírgula decimal é aceita

    def test_candles_que_comecam_depois_do_sinal_adiam_a_conferencia(self):
        k = candles((105, 95, 100), t0=T0 + 10 * HORA)
        r = av.avaliar(sinal(status="aberto"), k, T0 + 20 * HORA)
        self.assertEqual(r["status"], "aberto")
        self.assertIn("adiada", r["nota"])

    def test_busca_historico_quando_300h_nao_bastam(self):
        recentes = [[str(T0 + i * HORA), "1", "2", "0.5", "1"] for i in range(300, 600)]
        antigas = [[str(T0 + i * HORA), "1", "2", "0.5", "1"] for i in range(200, 300)]
        chamadas = []

        def falso_get(path):
            chamadas.append(path)
            if "history-candles" in path:
                return antigas if f"after={T0 + 300 * HORA}" in path else []
            return recentes
        orig = av.okx.get
        av.okx.get = falso_get
        try:
            k = av.candles_1h("BTC", desde=T0 + 250 * HORA)
        finally:
            av.okx.get = orig
        self.assertEqual(k["t"][0], T0 + 200 * HORA)
        self.assertEqual(len(k["t"]), 400)
        self.assertEqual(k["t"], sorted(k["t"]))


class TestArquivoEPlacar(unittest.TestCase):
    def test_id_repetido_conta_uma_vez(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([{"id": "a", "x": 1}, {"id": "a", "x": 2}, {"id": "b"}], f)
        try:
            self.assertEqual([s.get("x") for s in carregar_sinais(f.name)], [1, None])
        finally:
            os.unlink(f.name)

    def test_placar_conta_como_o_painel(self):
        sinais = [sinal(id="a", status="alvo2", resultado_r=2.25), sinal(id="b", status="stop", resultado_r=-1.0),
                  sinal(id="c", status="expirado", resultado_r=0), sinal(id="d", status="aberto"),
                  sinal(id="e", status="aguardando"), sinal(id="f", direcao="AGUARDAR")]
        self.assertEqual(ct.placar(sinais), {"publicados": 5, "acionados": 3, "encerrados": 2, "abertos": 1,
                                              "expirados": 1, "taxa_acerto": 50, "r_total": 1.25})


import monitor as mon  # noqa: E402


class TestMonitor(unittest.TestCase):
    """Quadro ao vivo e eventos (LONG: entrada 100, stop 90, alvo1 115, alvo2 130)."""

    def setUp(self):
        self.orig_get, self.orig_candles, self.orig_cg = mon.okx.get, av.candles_1h, mon.ids_coingecko
        mon.ids_coingecko = lambda simbolos: {"BTC": "bitcoin"}

    def tearDown(self):
        mon.okx.get, av.candles_1h, mon.ids_coingecko = self.orig_get, self.orig_candles, self.orig_cg

    def rodar(self, sinais, preco, k, anterior=None):
        mon.okx.get = lambda p: [{"instId": "BTC-USDT-SWAP", "last": str(preco)}] if "tickers" in p else None
        av.candles_1h = lambda ccy, desde=None: k
        return mon.monitorar(sinais, anterior or {}, agora_ms=T0 + 10 * HORA)

    def test_trade_aberto_mostra_flutuante_distancias_e_excursao(self):
        k = candles((108, 99, 105), (110, 96, 104))
        r = self.rodar([sinal(status="aberto")], 105.0, k)
        p = json.loads(r["ao_vivo"]["posicoes_json"])[0]
        self.assertEqual((p["status"], p["r_flutuante"]), ("aberto", 0.5))
        self.assertAlmostEqual(p["dist_stop_pct"], rd2(15 / 105 * 100))
        self.assertAlmostEqual(p["dist_alvo_pct"], rd2(10 / 105 * 100))
        self.assertEqual((p["mfe_r"], p["mae_r"]), (1.0, -0.4))
        self.assertEqual(p["prazo_fim"], iso(T0 + 168 * HORA))
        # 1% de 2000 = US$ 20 de risco; 0,5R = +10, menos a taxa de entrada (0,05% de 200)
        self.assertAlmostEqual(p["flutuante_usd"], 9.9)
        self.assertEqual(r["novos"], [])
        self.assertEqual(p["cg_id"], "bitcoin")

    def test_ids_coingecko_escolhe_o_maior_valor_de_mercado(self):
        import io
        resposta = json.dumps([{"symbol": "mon", "id": "monad", "market_cap": 100},
                               {"symbol": "mon", "id": "mon-falso", "market_cap": 5},
                               {"symbol": "vvv", "id": "venice-token", "market_cap": 9}]).encode()

        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        orig = mon.urllib.request.urlopen
        mon.urllib.request.urlopen = lambda req, timeout=None: R(resposta)
        try:
            self.assertEqual(self.orig_cg(["MON", "VVV", "XYZ"]), {"MON": "monad", "VVV": "venice-token"})
        finally:
            mon.urllib.request.urlopen = orig
        mon.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(OSError("sem rede"))
        try:
            self.assertEqual(self.orig_cg(["BTC"], espera=0), {})
        finally:
            mon.urllib.request.urlopen = orig

    def test_excursao_ignora_candles_antes_da_entrada(self):
        # o candle anterior à entrada foi a 130 (alvo 2), mas a operação só abriu no candle seguinte
        k = candles((130, 100, 101), (104, 98, 102), t0=T0)
        mfe, mae = mon.excursao(Plano(1, 100.0, 90.0, 115.0, 130.0), k, T0 + HORA, 102.0)
        self.assertEqual((mfe, mae), (0.4, -0.2))

    def test_codigos_do_coingecko_vem_da_memoria_e_so_busca_os_novos(self):
        pedidos = []
        mon.ids_coingecko = lambda simbolos: pedidos.append(list(simbolos)) or {}
        anterior = {"cg_ids": {"BTC": "bitcoin"}}
        r = self.rodar([sinal(status="aberto")], 105.0, candles((106, 99, 105)), anterior)
        self.assertEqual(pedidos, [])  # BTC já estava na memória
        self.assertEqual(json.loads(r["ao_vivo"]["posicoes_json"])[0]["cg_id"], "bitcoin")
        self.assertEqual(r["ao_vivo"]["cg_ids"], {"BTC": "bitcoin"})
        r = self.rodar([sinal(status="aberto")], 105.0, candles((106, 99, 105)), {})
        self.assertEqual(pedidos, [["BTC"]])
        self.assertEqual(r["sem_codigo"], ["BTC"])
        self.assertIn("sem código do CoinGecko para BTC", mon.resumo_texto(r))

    def test_ids_coingecko_tenta_de_novo(self):
        import io
        chamadas = []

        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def falso(req, timeout=None):
            chamadas.append(1)
            if len(chamadas) < 3:
                raise OSError("HTTP 429")
            return R(json.dumps([{"symbol": "btc", "id": "bitcoin", "market_cap": 1}]).encode())
        orig = mon.urllib.request.urlopen
        mon.urllib.request.urlopen = falso
        try:
            self.assertEqual(self.orig_cg(["BTC"], espera=0), {"BTC": "bitcoin"})
        finally:
            mon.urllib.request.urlopen = orig
        self.assertEqual(len(chamadas), 3)

    def test_aguardando_mostra_distancia_ate_a_entrada(self):
        s = sinal(tipo="condicional", entrada=95, stop=85, alvo1=110, alvo2=125, status="aguardando")
        r = self.rodar([s], 100.0, candles((101, 98, 100)))
        p = json.loads(r["ao_vivo"]["posicoes_json"])[0]
        self.assertEqual(p["status"], "aguardando")
        self.assertAlmostEqual(p["dist_entrada_pct"], 5.0)
        self.assertEqual(p["prazo_fim"], iso(T0 + 48 * HORA))

    def test_evento_de_stop_sai_da_lista_de_posicoes_e_guarda_historico(self):
        anterior = {"eventos_json": json.dumps([{"t": "x", "id": "velho", "texto": "antigo"}])}
        r = self.rodar([sinal(status="aberto")], 88.0, candles((101, 89, 92)), anterior)
        self.assertEqual([e["status"] for e in r["novos"]], ["stop"])
        self.assertEqual(r["novos"][0]["r"], -1.0)
        self.assertLess(r["novos"][0]["resultado_usd"], 0)
        self.assertEqual(json.loads(r["ao_vivo"]["posicoes_json"]), [])
        self.assertEqual([e["id"] for e in json.loads(r["ao_vivo"]["eventos_json"])], ["f1", "velho"])
        self.assertIn("stop", mon.resumo_texto(r))

    def test_gatilho_acionado_vira_evento(self):
        s = sinal(tipo="condicional", entrada=95, stop=85, alvo1=110, alvo2=125, status="aguardando")
        r = self.rodar([s], 97.0, candles((101, 94, 97)))
        self.assertEqual([e["status"] for e in r["novos"]], ["aberto"])

    def test_sem_preco_da_okx_usa_o_ultimo_candle(self):
        mon.okx.get = lambda p: None
        av.candles_1h = lambda ccy, desde=None: candles((106, 99, 103))
        r = mon.monitorar([sinal(status="aberto")], {}, agora_ms=T0 + 2 * HORA)
        self.assertEqual(json.loads(r["ao_vivo"]["posicoes_json"])[0]["preco"], 103)

    def test_ler_doc_aceita_envelope_e_arquivo_ausente(self):
        self.assertEqual(mon.ler_doc(None), {})
        self.assertEqual(mon.ler_doc("/nao/existe.json"), {})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"id": "atual", "data": {"eventos_json": "[]"}}, f)
        try:
            self.assertEqual(mon.ler_doc(f.name), {"eventos_json": "[]"})
        finally:
            os.unlink(f.name)


class TestCarteiraMesmoCandle(unittest.TestCase):
    def test_trade_que_abre_e_fecha_no_mesmo_candle_conta(self):
        # stop na primeira hora: acionado_em == encerrado_em; antes o prejuízo sumia (US$ 0)
        s = sinal(id="a", status="stop", resultado_r=-1.0, acionado_em="2026-11-20T01:00:00Z",
                  encerrado_em="2026-11-20T01:00:00Z")
        doc = ct.carteira([s], ct.Regras())
        self.assertAlmostEqual(doc["saldo"], rd2(2000 - 20 - 0.2))
        self.assertEqual(doc["abertas"], 0)

    def test_mesmo_candle_nao_muda_a_ordem_com_outros_trades(self):
        # "a" sai às 05h (aberto antes); "b" abre e fecha às 05h: "b" arrisca 1% do saldo já com "a"
        a = sinal(id="a", status="alvo2", resultado_r=2.25, acionado_em="2026-11-20T01:00:00Z", encerrado_em="2026-11-20T05:00:00Z")
        b = sinal(id="b", status="stop", resultado_r=-1.0, acionado_em="2026-11-20T05:00:00Z", encerrado_em="2026-11-20T05:00:00Z")
        extrato = json.loads(ct.carteira([b, a], ct.Regras())["extrato_json"])
        self.assertEqual([x["id"] for x in extrato], ["a", "b"])
        self.assertAlmostEqual(extrato[1]["risco_usd"], rd2(2044.8 * 0.01))
