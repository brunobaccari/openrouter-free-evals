import html
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def evaluation_summary(rows, expected):
    failures = sum(bool(row['errors']) for row in rows)
    lines = [f'Executados: **{len(rows)}/{expected}**; passaram: **{len(rows) - failures}**; falharam: **{failures}**.']
    if len(rows) != expected:
        lines.append('**Lote incompleto: não representa aprovação.**')
    lines += ['', '| Caso | Resultado |', '| --- | --- |']
    for row in rows:
        case = html.escape(str(row['case_id'])).replace('|', '&#124;').replace('\n', ' ')
        result = html.escape(', '.join(row['errors']) or 'PASS').replace('|', '&#124;').replace('\n', ' ')
        lines.append(f'| {case} | {result} |')
    return lines


def main():
    live = sys.argv[1] == 'live'
    invalid_report = False
    lines = ['## Avaliação live — OpenRouter' if live else '## Avaliador e corpus manual', '']
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
            expected = len(json.loads(Path('fixtures/cases.json').read_text(encoding='utf-8')))
            lines += evaluation_summary(rows, expected)
            if live:
                lines += ['', 'Respostas, modelo, provedor, horário e uso informado estão no artifact; o resumo não reproduz prompts nem respostas.']
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
