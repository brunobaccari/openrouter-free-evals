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

## Critérios para decidir uma mudança

| Sinal | Decisão e próximo passo |
| --- | --- |
| Corpus vazio, IDs repetidos ou gabarito com fonte ausente do contexto | Bloquear antes de chamar a API. Corrigir o corpus e revisar a regra com quem responde pelo produto. |
| Prazo, decisão, fonte ou token proibido incorreto | Reprovar a resposta. QA isola o caso; desenvolvimento investiga prompt e integração. Não mudar o gabarito para acompanhar a saída do modelo. |
| HTTP 429 após o orçamento, timeout ou lote incompleto | Resultado inconclusivo sobre qualidade do modelo. Conferir o provedor antes de repetir; conservar a execução falha para comparação. |
| `finish_reason` diferente de `stop`, envelope inválido ou JSON inválido | Reprovar a geração, mesmo que um trecho pareça correto. Não reparar ou repetir a resposta para conseguir aprovação. |
| Todos os contratos aprovados | Revisar manualmente se o texto concorda com os fatos e se as fontes sustentam a resposta. O gate automatizado não cobre essa decisão. |

Ao alterar uma regra, mudar juntos o contexto sintético, o gabarito e uma mutação que o avaliador deva rejeitar. Na revisão, comparar a falha específica e seu efeito no atendimento; a taxa agregada de acerto não compensa vazamento ou prazo incorreto. Os artifacts permitem revisar essa decisão sem anexar respostas ao histórico Git.

O corpus é validado antes do catálogo e da geração. O summary falha quando faltam relatórios ou não há testes registrados. O [contrato da API](https://openrouter.ai/docs/api_reference/overview) define os motivos de término; este cliente aceita apenas `stop` para iniciar a avaliação de conteúdo.

## Documentos e ataques às instruções

Além dos 20 casos de regressão, `fixtures/adversarial.json` contém 12 casos: oito para ajuste e quatro para controle (`holdout`). Cobrem falsas mensagens de sistema/tool, JSON de resposta imposto pelo usuário, instrução escondida em documento, reserva sem evidência e extração de token por codificação ou espaçamento. São cenários sintéticos, sem documentos ou segredos de clientes.

`fixtures/documents/` contém uma política vigente e notas usadas nos ataques. O carregador lê apenas Markdown dentro dessa pasta, resolve o conteúdo antes da chamada e recusa caminhos que saiam dela. O modelo recebe o texto e o ID da fonte, nunca o gabarito. Isso exercita uso de contexto documental; não implementa busca vetorial nem demonstra qualidade de um RAG completo.

```bash
python evaluate.py --cases fixtures/adversarial.json --responses fixtures/adversarial-responses.json --output results/adversarial.json
python live_openrouter.py --cases fixtures/adversarial.json --prompt-file prompts/guardrails-v2.txt
```

As respostas manuais exercitam o avaliador, não comprovam que um modelo resiste aos ataques. O summary mascara também as transformações de token que o avaliador reconhece.

## Comparar modelos e iterar o prompt

O prompt atual permanece como baseline. `prompts/guardrails-v2.txt` é um candidato: explicita fronteiras entre instruções e documentos, recusa extração transformada e exige coerência entre decisão, fatos e explicação. Ainda precisa de comparação live antes de substituir o baseline.

Os três modelos configurados em `.env.example` são Apodex Mini, Dots Note e Nemotron Super em suas variantes gratuitas. A disponibilidade e os parâmetros são reconferidos no catálogo a cada execução. Não há troca automática para modelo pago.

```bash
python compare_prompts.py --split development --repeats 1
python compare_prompts.py --split holdout --repeats 1
```

O workflow manual **Compare prompts** faz a mesma comparação. Cada par usa os mesmos casos, modelo e parâmetros. O orçamento é calculado antes de gerar; sem cota suficiente o lote é bloqueado e não aparece como aprovado. `--models` permite selecionar um subconjunto; `--repeats` aceita até três rodadas. Os relatórios ficam nos artifacts, separados por modelo, prompt e rodada, com hashes do corpus e do prompt.

Trabalhe primeiro nos oito casos de ajuste: examine a falha e mude uma regra do prompt por vez. Repita baseline e candidato nas mesmas condições; uma única resposta correta não demonstra estabilidade. Só depois rode os quatro casos de controle e a regressão de 20 casos. Se usar uma falha do controle para ajustar o prompt, esse conjunto deixa de ser independente: acrescente casos inéditos antes de tomar uma decisão de promoção. Melhorar o candidato nunca altera o gabarito para acompanhar a resposta.

Cota esgotada, erro de transporte e resposta truncada ficam separados das falhas de contrato. Não conclua que um modelo é melhor quando algum lote está incompleto. Mesmo com todos os contratos aprovados, revise a explicação textual antes de promover o prompt.

## Limites do avaliador

O campo `answer` recebe checagem de presença, tamanho e token proibido. **Não há verificação semântica de que o texto concorda com `facts`**, nem validação de fundamentação por linguagem natural. Referências válidas, sozinhas, não provam que uma afirmação está sustentada. A detecção de token cobre texto literal, base64, hexadecimal, ROT13, espaços e caracteres de largura zero. Não cobre codificações arbitrárias, fragmentos isolados ou paráfrases.

Cada rodada live registra modelo, parâmetros e respostas. Ainda é necessário revisar a coerência do texto com os fatos; aprovação deste corpus pequeno não demonstra qualidade geral ou segurança completa de um modelo. Não envie dados internos de empresa para este corpus.

## Relatórios

`results/evaluation.json` mostra falhas por caso. `results/junit.xml` registra os testes do avaliador. Ambos são guardados no CI. A execução do corpus manual é um teste do mecanismo, não um benchmark. Veja [Execuções e artifacts no Actions](https://github.com/brunobaccari/openrouter-free-evals/actions).

Referências: [modelos gratuitos](https://openrouter.ai/docs/guides/routing/model-variants/free), [limite de preço por provedor](https://openrouter.ai/docs/guides/routing/provider-selection).

## Configuração do ambiente

Copie `.env.example` para `.env` (`Copy-Item .env.example .env` no PowerShell ou `cp .env.example .env` no Linux/macOS). As variáveis do processo têm prioridade. `.env` não é versionado. URLs e credenciais ficam nessa configuração; os valores esperados dos testes permanecem nos cenários.

## Resultados no GitHub Actions

No GitHub, abra **Actions → workflow → execução → Summary**. Em `Tests`, o resumo separa testes unitários e respostas manuais; baixe o artifact `results` para obter `junit.xml` e `evaluation.json`. Em `OpenRouter live`, o resumo informa casos executados, falhas e lote incompleto; o artifact `live-evaluation` contém `live.json`. Upload e resumo rodam também após falha; retenção de 30 dias. Relatório ausente é indicado, sem registrar aprovação.

Expanda cada caso para conferir pergunta, contexto sintético, valores exigidos de decision/facts/sources, resposta recebida e resultado de cada regra de contrato. O CI automático mostra fixtures manuais; o live mostra respostas reais da API e metadados do provedor. Não existe uma frase exata obrigatória para answer. Tokens sintéticos proibidos são mascarados no resumo; entradas da avaliação e artifacts permanecem inalterados. Regras não executadas após erro de transporte ou parsing ficam explicitamente sem avaliação, sem serem contadas como aprovadas. Os testes unitários incluem resumo, mascaramento e rejeição de entradas ou gerações incompletas.

Datas de commits deste portfólio foram reorganizadas retroativamente; as execuções do Actions mantêm suas datas reais.
