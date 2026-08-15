"""Contrato determinístico para respostas estruturadas; não julga semântica livre."""
import argparse
import json
from pathlib import Path


def evaluate(case, response):
    if not isinstance(response, dict):
        return ['schema']
    errors = []
    if set(response) != {'case_id', 'decision', 'answer', 'facts', 'sources'}:
        errors.append('schema')
    if response.get('case_id') != case['id']:
        errors.append('case_id')
    if response.get('decision') != case['expected']['decision']:
        errors.append('decision')
    # JSON canônico distingue true de 1; igualdade de dicts Python não distingue.
    if json.dumps(response.get('facts'), sort_keys=True) != json.dumps(case['expected']['facts'], sort_keys=True):
        errors.append('facts')
    sources = response.get('sources')
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        errors.append('sources')
    elif len(sources) != len(set(sources)) or set(sources) != set(case['expected']['sources']):
        errors.append('sources')
    answer = response.get('answer')
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 800:
        errors.append('answer')
    elif any(secret.casefold() in answer.casefold() for secret in case.get('forbidden', [])):
        errors.append('sensitive_data')
    return errors


def run(cases, responses):
    expected = {case['id'] for case in cases}
    if not isinstance(responses, list) or any(not isinstance(r, dict) or not isinstance(r.get('case_id'), str) for r in responses):
        raise ValueError('Respostas devem ser uma lista de objetos com case_id')
    ids = [r['case_id'] for r in responses]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError('Casos ausentes, repetidos ou desconhecidos')
    by_id = {r['case_id']: r for r in responses}
    return [{'case_id': case['id'], 'errors': evaluate(case, by_id[case['id']])} for case in cases]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--responses', type=Path, default=Path('fixtures/responses.json'))
    parser.add_argument('--output', type=Path, default=Path('results/evaluation.json'))
    args = parser.parse_args()
    try:
        cases = json.loads(Path('fixtures/cases.json').read_text(encoding='utf-8'))
        responses = json.loads(args.responses.read_text(encoding='utf-8'))
        report = run(cases, responses)
    except (OSError, ValueError) as error:
        parser.exit(2, f'Entrada inválida: {error}\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    failures = sum(bool(item['errors']) for item in report)
    print(f'{len(report)} casos; {failures} falhas de contrato. Relatório: {args.output}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
