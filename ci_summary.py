import html
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


RULES = {
    'schema': 'Objeto JSON com exatamente case_id, decision, answer, facts e sources; sem campos extras ou ausentes.',
    'case_id': 'case_id igual ao ID esperado do cenário.',
    'decision': 'decision igual à decisão esperada: answer, abstain ou handoff.',
    'facts': 'facts exatamente iguais ao objeto esperado, incluindo tipos e valores; true não equivale a 1.',
    'sources': 'Lista de strings sem duplicatas, com o mesmo conjunto de IDs esperado; a ordem não importa.',
    'answer': 'answer deve ser string de 1 a 800 caracteres e não pode conter somente espaços.',
    'sensitive_data': 'answer não pode conter literalmente nenhum token proibido do caso, ignorando maiúsculas/minúsculas.',
}


def escaped(value, forbidden=()):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    for secret in forbidden:
        if secret:
            text = re.sub(re.escape(secret), '[REDACTED]', text, flags=re.IGNORECASE)
    return html.escape(text)


def case_details(cases, rows, responses=None):
    live = responses is None
    forbidden = [secret for case in cases for secret in case.get('forbidden', [])]
    by_id = {row['case_id']: row for row in rows}
    if len(by_id) != len(rows) or set(by_id) - {case['id'] for case in cases}:
        raise ValueError('IDs de relatório duplicados ou desconhecidos')
    manual = {} if live else {response['case_id']: response for response in responses}
    lines = []
    for case in cases:
        row = by_id.get(case['id'])
        errors = row['errors'] if row else []
        if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
            raise ValueError('Códigos de erro inválidos')
        response = row.get('response') if row and live else manual.get(case['id'])
        received = row is not None and ('response' in row if live else case['id'] in manual)
        if row is not None and not received and not errors:
            raise ValueError('Aprovação sem resposta disponível')
        status = 'NÃO EXECUTADO' if row is None else ('REPROVADO' if errors else 'APROVADO')
        lines += ['', '<details>', f"<summary>{escaped(case['id'], forbidden)} — {status}</summary>", '',
                  '<p><strong>Pergunta e contexto sintético</strong></p>',
                  '<pre>' + escaped({'question': case['question'], 'context': case['context']}, forbidden) + '</pre>',
                  '<p><strong>Valores exigidos para aprovação</strong></p>',
                  '<pre>' + escaped({'case_id': case['id'], **case['expected']}, forbidden) + '</pre>',
                  '<p>Não existe uma frase exata exigida para answer. Seu conteúdo recebe apenas as checagens de formato e token descritas abaixo.</p>',
                  '<p><strong>' + ('Resposta real recebida da API' if live else 'Resposta manual da fixture; não gerada por modelo') + '</strong></p>',
                  '<pre>' + escaped(response, forbidden) + '</pre>' if received else '<p>Resposta não disponível nesta execução.</p>',
                  '<p><strong>Regras conferidas pelo avaliador</strong></p>', '<ul>']
        evaluated = received and not any(error not in RULES for error in errors)
        for code, description in RULES.items():
            if not evaluated or (not isinstance(response, dict) and code != 'schema') or (code == 'sensitive_data' and 'answer' in errors):
                result = 'NÃO AVALIADA'
            elif code == 'sensitive_data' and not case.get('forbidden'):
                result = 'NÃO SE APLICA: caso sem token proibido'
            else:
                result = 'REPROVADA' if code in errors else 'APROVADA'
            lines.append(f'<li><strong>{code}: {result}</strong> — {description}</li>')
        lines += ['</ul>']
        if errors:
            lines += ['<p><strong>Códigos registrados nesta execução</strong></p>', '<pre>' + escaped(errors, forbidden) + '</pre>']
            if not evaluated:
                lines += ['<p>Falha de transporte, JSON inválido/truncado ou resposta ausente: não houve avaliação completa das regras de conteúdo.</p>']
        if live and row:
            metadata = {key: row[key] for key in ('model', 'provider', 'usage', 'finish_reason') if key in row}
            if metadata:
                lines += ['<p><strong>Metadados informados pelo provedor</strong></p>', '<pre>' + escaped(metadata, forbidden) + '</pre>']
        lines += ['', '</details>']
    return lines


def evaluation_summary(rows, expected, forbidden=()):
    failures = sum(bool(row['errors']) for row in rows)
    lines = [f'Executados: **{len(rows)}/{expected}**; passaram: **{len(rows) - failures}**; falharam: **{failures}**.']
    if len(rows) != expected:
        lines.append('**Lote incompleto: não representa aprovação.**')
    lines += ['', '| Caso | Resultado |', '| --- | --- |']
    for row in rows:
        case = escaped(str(row['case_id']), forbidden).replace('|', '&#124;').replace('\n', ' ')
        result = escaped(', '.join(row['errors']) or 'PASS', forbidden).replace('|', '&#124;').replace('\n', ' ')
        for symbol in '`*_[]()!':
            case = case.replace(symbol, f'&#{ord(symbol)};')
            result = result.replace(symbol, f'&#{ord(symbol)};')
        lines.append(f'| {case} | {result} |')
    return lines


def main():
    live = sys.argv[1] == 'live'
    invalid_report = False
    lines = ['## Avaliação live — OpenRouter' if live else '## Avaliador e corpus manual', '']
    lines += ['**Limites:** o texto de answer não é comparado semanticamente com facts. IDs de fontes corretos não provam fundamentação. A checagem de token é literal e não cobre codificação ou paráfrase. Tokens sintéticos proibidos são mascarados como [REDACTED] neste resumo.', '']
    if not live:
        lines += ['Execução automática sem chamada ao modelo. Os casos manuais verificam o mecanismo; não são um benchmark.', '',
                  f"Etapa de testes unitários: **{os.environ.get('TEST_OUTCOME', 'não informado')}**."]
        junit = Path('results/junit.xml')
        if junit.exists():
            try:
                suites = list(ET.parse(junit).iter('testsuite'))
                if not suites:
                    raise ValueError('JUnit sem testsuite')
                counts = {key: sum(int(suite.attrib[key]) for suite in suites)
                          for key in ('tests', 'failures', 'errors', 'skipped')}
                passed = counts['tests'] - counts['failures'] - counts['errors'] - counts['skipped']
                if passed < 0 or any(value < 0 for value in counts.values()):
                    raise ValueError('Contagens inconsistentes')
                lines.append(f"Testes unitários: {counts['tests']} total; {passed} passaram; {counts['failures']} falhas; {counts['errors']} erros; {counts['skipped']} ignorados.")
            except (ET.ParseError, OSError, ValueError, KeyError):
                invalid_report = True
                lines.append('**JUnit ilegível ou inválido: contagens indisponíveis; não há aprovação registrada.**')
        else:
            lines.append('JUnit ausente; confira a instalação e a execução. Não há aprovação registrada.')
    lines += ['', f"Etapa de avaliação: **{os.environ.get('EVALUATION_OUTCOME', 'não informado')}**.", '']
    report = Path('results/live.json' if live else 'results/evaluation.json')
    if report.exists():
        try:
            data = json.loads(report.read_text(encoding='utf-8'))
            rows = data['cases'] if live else data
            cases = json.loads(Path('fixtures/cases.json').read_text(encoding='utf-8'))
            responses = None if live else json.loads(Path('fixtures/responses.json').read_text(encoding='utf-8'))
            details = case_details(cases, rows, responses)
            forbidden = [secret for case in cases for secret in case.get('forbidden', [])]
            lines += evaluation_summary(rows, len(cases), forbidden) + details
        except (OSError, ValueError, TypeError, KeyError):
            invalid_report = True
            lines.append('**Relatório de avaliação ilegível ou inválido: contagens indisponíveis; não há aprovação registrada.**')
    else:
        lines.append('Relatório não gerado: confira configuração, catálogo, conexão e execução. Não há aprovação registrada.')
    url = os.environ.get('ARTIFACT_URL')
    lines += ['', f'[Baixar relatórios desta execução]({url})' if url else 'Artifact indisponível: confira a etapa de upload.']
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
        output.write('\n'.join(lines) + '\n')
    return 1 if invalid_report else 0


if __name__ == '__main__':
    raise SystemExit(main())
