# Instruções de cada rodada

Este é o roteiro que a tarefa agendada (a cada 4 horas) entrega a uma sessão nova do Claude.

A cada rodada, a sessão baixa a versão mais nova de `scripts/analise_tecnica.py` e `scripts/avaliar_sinais.py` do branch `main` deste repositório, primeiro com `git clone --depth 1` e, se falhar, pelo raw.githubusercontent.com. Ela anota o commit usado nas fontes do relatório. Para mudar a análise técnica, basta fazer commit no `main`: a próxima rodada já usa a versão nova. Se o script não baixar ou não rodar, a rodada publica os futuros como "AGUARDAR" em vez de improvisar outro cálculo.

1. **Memória:** ler as últimas 6 rodadas (`data/radar/briefings`), os últimos sinais de futuros (`data/radar/futuros`), o banco de ideias (`data/radar/ideias`) e as oportunidades de renda digital (`data/radar/renda`).
2. **Dados de mercado:** CoinGecko `coins/markets`, `global` e `search/trending`; alternative.me `fng`; DeFiLlama `v2/chains`.
3. **Análise gráfica:** `python3 scripts/analise_tecnica.py BTC ETH SOL XRP BNB DOGE`.
   **Conferência:** `python3 scripts/avaliar_sinais.py sinais.json` com todos os sinais do painel. O resultado vai para os campos `status`, `resultado_r`, `acionado_em`, `encerrado_em` e `verificado_em` de cada sinal. A rodada não mexe na entrada, no stop, nos alvos nem na tese de sinais antigos.
4. **Notícias só de fontes fortes** (busca restrita por domínio):
   - Cripto: coindesk.com, theblock.co, decrypt.co, bloomberg.com, cnbc.com, sec.gov, farside.co.uk
   - Negócios: techcrunch.com, news.crunchbase.com, bloomberg.com, cnbc.com, theinformation.com
   - Brasil: valor.globo.com, infomoney.com.br, bcb.gov.br, gov.br, neofeed.com.br, braziljournal.com
   - Renda digital: techcrunch.com, theverge.com, businessinsider.com, cnbc.com, bloomberg.com, reuters.com, exame.com, valor.globo.com, infomoney.com.br, sebrae.com.br, neofeed.com.br, braziljournal.com, blog.youtube, shopify.com, stripe.com
   - Proibido: agregadores, SEO, cursos de "renda extra", páginas de venda de infoproduto, "price prediction", Medium, redes sociais. Toda notícia leva a fonte e a data.
5. **Os cinco agentes:** analista de mercado, caçador de negócios, analista técnico de futuros, caçador de renda digital e gestor de risco (regras no README). O caçador de renda digital traz de 1 a 3 oportunidades novas por rodada, cada uma com fonte forte, custo para começar, prazo estimado até a primeira renda e 3 primeiros passos; se não houver nada novo e confiável, diz isso em vez de forçar.
6. **Gravar** documentos novos nas cinco coleções (`briefings`, `sinais`, `ideias`, `renda`, `futuros`), com o mesmo `at` para a rodada inteira.
7. **Relatório final** (vai por e-mail e push): resumo, mercado, sinais de futuros, placar dos sinais, negócios, renda digital, riscos, fontes e o link do painel. Começa com "ALERTA:" quando o nível é alerta.
