# OpenRouter — avaliação de respostas com Python

[English version](README.en.md)

Exemplo de avaliação de respostas estruturadas para um assistente de atendimento. Confere fatos esperados, decisão de responder ou abster-se, referências e vazamento de um segredo sintético.

A execução real usa a API hospedada da OpenRouter, somente com modelos `:free` e preço zero confirmado no catálogo. O corpus de referência é manual; o gabarito não é enviado ao modelo.

## Instalação

Python 3.12 ou superior (CI usa 3.14).

```bash
python -m venv .venv
```

Ative com `.venv\Scripts\activate` no Windows ou `source .venv/bin/activate` no Linux/macOS.

```bash
cp .env.example .env
python -m pip install -r requirements.txt
python -m pytest -q --junitxml=results/junit.xml
python evaluate.py
```

Para avaliar outro arquivo com o mesmo contrato e os mesmos IDs do corpus:

```bash
python evaluate.py --responses respostas.json --output results/avaliacao.json
```

Código de saída: `0` quando todos os contratos passam, `1` quando uma resposta falha e `2` para entrada inválida. Casos ausentes ou duplicados não são aceitos como uma execução completa.

## Contrato enviado ao modelo

O cliente exige `json_schema` com os cinco campos obrigatórios e `require_parameters: true`. O schema descreve tipos e campos permitidos; não contém prazos, fontes esperadas ou a decisão correta de cada caso. O gabarito continua apenas no avaliador. O catálogo precisa indicar suporte a saída estruturada e raciocínio opcional.

Para esta tarefa de extração, o raciocínio opcional fica desativado: a rodada anterior gastou o orçamento em reasoning e devolveu conteúdo vazio ou truncado. O limite de saída continua em 4.096 tokens. A regra de abstenção exige sempre `facts: {}` e `sources: []`, inclusive quando o documento explica a ausência de informação. As respostas continuam sendo avaliadas integralmente, sem preenchimento ou reparo local.

Referências: [saída estruturada](https://openrouter.ai/docs/guides/features/structured-outputs) e [orçamento de raciocínio](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Executar contra a API hospedada

```bash
python live_openrouter.py --model apodex/apodex-1.1-mini:free
```

Informe a chave no prompt oculto, ou configure `OPENROUTER_API_KEY` no ambiente. Não coloque a chave em arquivo versionado. O cliente consulta o catálogo antes de gerar, recusa preço diferente de zero e envia teto zero para prompt, resposta e requisição, com fallback desativado.

O relatório `results/live.json` registra horário, modelo, provedor, uso informado, resposta e falhas por caso. Erro HTTP ou lote incompleto reprova a execução. Somente HTTP 429 tem retry limitado; não há troca silenciosa de modelo. Limites de uso do provedor podem impedir uma rodada gratuita.

O CI automático testa o avaliador e as proteções de custo, sem chamadas a modelo. O workflow manual `OpenRouter live` executa a API quando o secret `OPENROUTER_API_KEY` estiver configurado no repositório. O secret está configurado no GitHub Actions; seu valor não faz parte dos arquivos do projeto.

## Resiliência a HTTP 429

O catálogo e a geração usam a mesma política: uma chamada inicial e até três retries por requisição. `Retry-After` aceita segundos ou data HTTP. Sem header válido, a espera cresce a partir de 5 segundos, com jitter e teto de 60 segundos por espera.

O orçamento de espera é **120 segundos para o lote inteiro**, compartilhado entre catálogo e cenários. Se o servidor pedir mais que o tempo restante, o cliente encerra sem tentar antes do prazo. Cota diária esgotada pode continuar retornando 429; retry não remove esse limite.

Configure no `.env` (valores padrão em `.env.example`):

| Variável | Padrão | Limite aceito |
| --- | --- | --- |
| `OPENROUTER_MAX_RETRIES` | 3 | 0–5 por requisição; 0 desativa |
| `OPENROUTER_RETRY_BASE_SECONDS` | 5 | 1–60 segundos |
| `OPENROUTER_RETRY_BUDGET_SECONDS` | 120 | 0–300 segundos por execução |

O artifact e o summary registram tentativas, esperas e motivo de encerramento (`retry_limit` ou `wait_budget`). Erros de contrato, JSON inválido, outros status HTTP e timeouts não provocam retry. O modelo, o contexto, o gabarito e as proteções de custo permanecem os mesmos durante as tentativas. Se o 429 persistir, o workflow falha e preserva o lote incompleto.

Os testes de transporte simulam o 429 e substituem o relógio de espera: conferem recuperação, esgotamento, header inválido, data HTTP, erros não elegíveis e ausência de retry diante de uma resposta que viola o contrato.

Referência: [orientação de rate limits da OpenRouter](https://openrouter.ai/docs/api_reference/limits), consultada em 06/10/2026.

## Estrutura

- `fixtures/cases.json`: pergunta, contexto e resultado esperado por caso.
- `fixtures/responses.json`: respostas manuais para conferir o avaliador.
- `evaluate.py`: validação e relatório por caso.
- `live_openrouter.py`: geração na API hospedada e comparação com o corpus.
- `tests/test_free_guard.py`: recusa de modelos pagos e gabarito separado do prompt.
- `tests/test_evaluate.py`: casos válidos e mutações que precisam ser detectadas.

## Casos

| Grupo | Cenários |
| --- | --- |
| Prazos e regras | Reembolso de 14 dias; prazo zero; cancelamento e reembolso na mesma pergunta; prazo negado e corrigido. |
| Contexto aplicável | Produto diferente; plano Premium; plano desconhecido; números de atendimento irrelevantes. |
| Fontes | Política revogada versus vigente; duas fontes concordantes; divergência de reembolso; divergência de cancelamento. |
| Informação insuficiente | Reserva sem evidência; documento sem prazo. |
| Idioma | Pergunta e documento em inglês, resposta solicitada em português. |
| Instruções indevidas | Comando dentro de documento; falsa mensagem de sistema; pedido do usuário para ignorar a política. |
| Informação interna | Pedido de token; alegação de auditoria para extrair instrução e token sintético. |

São **20 cenários**, cada um com gabarito separado da mensagem enviada ao modelo. As fontes esperadas incluem todos os documentos vigentes e aplicáveis; uma política revogada ou de outro produto não sustenta a resposta. Falta do plano é insuficiência de informação, não conflito entre políticas.

Os testes alteram prazo, tipo do valor, referência, decisão e texto sensível para comprovar que essas falhas são detectadas. Um teste também executa o CLI e exige saída 1 diante de uma resposta errada.

## Limites do avaliador

O campo `answer` recebe checagem de presença, tamanho e token proibido. **Não há verificação semântica de que o texto concorda com `facts`**, nem validação de fundamentação por linguagem natural. Referências válidas, sozinhas, não provam que uma afirmação está sustentada. A detecção de dado sensível é literal e não cobre codificação ou paráfrase.

Cada rodada live registra modelo, parâmetros e respostas. Ainda é necessário revisar a coerência do texto com os fatos; aprovação deste corpus pequeno não demonstra qualidade geral ou segurança completa de um modelo. Não envie dados internos de empresa para este corpus.

## Relatórios

`results/evaluation.json` mostra falhas por caso. `results/junit.xml` registra os testes do avaliador. Ambos são guardados no CI. A execução do corpus manual é um teste do mecanismo, não um benchmark. Veja [Execuções e artifacts no Actions](https://github.com/brunobaccari/openrouter-free-evals/actions).

Referências: [modelos gratuitos](https://openrouter.ai/docs/guides/routing/model-variants/free), [limite de preço por provedor](https://openrouter.ai/docs/guides/routing/provider-selection).

## Configuração do ambiente

Copie `.env.example` para `.env` (`Copy-Item .env.example .env` no PowerShell ou `cp .env.example .env` no Linux/macOS). As variáveis do processo têm prioridade. `.env` não é versionado. URLs e credenciais ficam nessa configuração; os valores esperados dos testes permanecem nos cenários.

## Resultados no GitHub Actions

No GitHub, abra **Actions → workflow → execução → Summary**. Em `Tests`, o resumo separa testes unitários e respostas manuais; baixe o artifact `results` para obter `junit.xml` e `evaluation.json`. Em `OpenRouter live`, o resumo informa casos executados, falhas e lote incompleto; o artifact `live-evaluation` contém `live.json`. Upload e resumo rodam também após falha; retenção de 30 dias. Relatório ausente é indicado, sem registrar aprovação.

Expanda cada caso para conferir pergunta, contexto sintético, valores exigidos de decision/facts/sources, resposta recebida e resultado de cada regra de contrato. O CI automático mostra fixtures manuais; o live mostra respostas reais da API e metadados do provedor. Não existe uma frase exata obrigatória para answer. Tokens sintéticos proibidos são mascarados no resumo; entradas da avaliação e artifacts permanecem inalterados. Regras não executadas após erro de transporte ou parsing ficam explicitamente sem avaliação, sem serem contadas como aprovadas. São 70 testes unitários, incluindo os checks de resumo e mascaramento.

Datas de commits deste portfólio foram reorganizadas retroativamente; as execuções do Actions mantêm suas datas reais.
