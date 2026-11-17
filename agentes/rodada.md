# Instruções de cada rodada

Este é o roteiro que a tarefa agendada (a cada 4 horas) entrega a uma sessão nova do Claude. A versão em produção traz o script `scripts/analise_tecnica.py` embutido.

1. **Memória:** ler as últimas 6 rodadas (`data/radar/briefings`), os últimos sinais de futuros (`data/radar/futuros`) e o banco de ideias (`data/radar/ideias`).
2. **Dados de mercado:** CoinGecko `coins/markets`, `global` e `search/trending`; alternative.me `fng`; DeFiLlama `v2/chains`.
3. **Análise gráfica:** `python3 scripts/analise_tecnica.py BTC ETH SOL XRP BNB DOGE`.
4. **Notícias só de fontes fortes** (busca restrita por domínio):
   - Cripto: coindesk.com, theblock.co, decrypt.co, bloomberg.com, cnbc.com, sec.gov, farside.co.uk
   - Negócios: techcrunch.com, news.crunchbase.com, bloomberg.com, cnbc.com, theinformation.com
   - Brasil: valor.globo.com, infomoney.com.br, bcb.gov.br, gov.br, neofeed.com.br, braziljournal.com
   - Proibido: agregadores, SEO, "price prediction", Medium, redes sociais. Toda notícia leva a fonte e a data.
5. **Os quatro agentes:** analista de mercado, caçador de negócios, analista técnico de futuros e gestor de risco (regras no README).
6. **Gravar** documentos novos nas quatro coleções, com o mesmo `at` para a rodada inteira.
7. **Relatório final** (vai por e-mail e push): resumo, mercado, sinais de futuros com o resultado dos anteriores, negócios, riscos, fontes e o link do painel. Começa com "ALERTA:" quando o nível é alerta.
