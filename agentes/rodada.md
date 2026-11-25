# Instruções de cada rodada

Este é o roteiro que a tarefa agendada (a cada 4 horas) entrega a uma sessão nova do Claude.

A cada rodada, a sessão baixa a versão mais nova da pasta `scripts/` (`analise_tecnica.py`, `avaliar_sinais.py`, `carteira.py` e os módulos `indicadores.py` e `radar_comum.py` que eles importam) do branch `main` deste repositório, primeiro com `git clone --depth 1` e, se falhar, pelo raw.githubusercontent.com. Ela anota o commit usado nas fontes do relatório. Para mudar a análise técnica, basta fazer commit no `main`: a próxima rodada já usa a versão nova. Se o script não baixar ou não rodar, a rodada publica os futuros como "AGUARDAR" em vez de improvisar outro cálculo.

1. **Memória:** ler as últimas 6 rodadas (`data/radar/briefings`), todos os sinais de futuros (`data/radar/futuros`) e as oportunidades de renda já publicadas (`data/radar/renda`).
2. **Dados de mercado:** CoinGecko `coins/markets`, `global` e `search/trending`; alternative.me `fng`; DeFiLlama `v2/chains`.
3. **Análise gráfica (radar de todas as moedas):** `python3 scripts/analise_tecnica.py --todas --saida ta.json --scanner scanner.json > resumo.json`. A tabela `scanner.json` vai para o painel (aba Radar). O resumo traz o **Top 10**: as moedas com maior nota de qualidade (backtest do plano, confluência, RSI, liquidez e posicionamento), com mais de US$ 5 milhões por dia e sem backtest negativo. O analista técnico só escolhe sinais entre elas.
   **Conferência:** `python3 scripts/avaliar_sinais.py sinais.json` com todos os sinais do painel. O resultado vai para os campos `status`, `resultado_r`, `acionado_em`, `encerrado_em` e `verificado_em` de cada sinal. A rodada não mexe na entrada, no stop, nos alvos nem na tese de sinais antigos.
   **Carteira:** `python3 scripts/carteira.py sinais_conferidos.json > carteira.json` com os sinais já conferidos. Grava em `data/radar/carteira/atual` o saldo da conta fictícia de US$ 2.000 (1% de risco por operação, taxa de 0,05% por lado).
4. **Notícias só de fontes fortes** (busca restrita por domínio):
   - Cripto: coindesk.com, theblock.co, decrypt.co, bloomberg.com, cnbc.com, sec.gov, farside.co.uk
   - Renda digital: techcrunch.com, theverge.com, businessinsider.com, reuters.com, bloomberg.com, cnbc.com, exame.com, sebrae.com.br, blog.youtube, shopify.com, stripe.com
   - Brasil: valor.globo.com, infomoney.com.br, bcb.gov.br, gov.br, neofeed.com.br, braziljournal.com
   - Proibido: agregadores, SEO, "price prediction", Medium, redes sociais. Toda notícia leva a fonte e a data.
5. **Os agentes:** analista de mercado (também define o nível e os riscos), analista técnico de futuros (decide sozinho os sinais, só entre o Top 10, de 0 a 4 por rodada) e caçador de renda digital (regras no README).
6. **Gravar** o radar, a carteira e os documentos novos de rodada, sinais, renda e futuros, com o mesmo `at` para a rodada inteira.
   **Arquivamento:** o painel lê no máximo 1.000 registros por coleção. Por isso, a cada rodada, os registros "AGUARDAR" de futuros com mais de 3 dias vão para `data/radar/arquivo_futuros` e os sinais de mercado com mais de 10 dias vão para `data/radar/arquivo_sinais`. A cópia e a remoção do original acontecem juntas, numa única gravação que só vale inteira. Sinais LONG/SHORT, rodadas, ideias e renda nunca são arquivados.
7. **Relatório final** (vai por e-mail e push): resumo, saldo da carteira fictícia, mercado, Top 10 do radar, sinais de futuros, placar dos sinais, renda digital, riscos, fontes e o link do painel. Começa com "ALERTA:" quando o nível é alerta.
