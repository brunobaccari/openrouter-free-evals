"""Contrato determinístico para respostas estruturadas; não julga semântica livre."""
import argparse
import base64
import codecs
import json
from pathlib import Path
import re


def load_cases(path):
    path = Path(path)
    cases = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(cases, list):
        return validate_cases(cases)
    document_root = (path.parent / 'documents').resolve()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('context'), list):
            continue
        for document in case['context']:
            if not isinstance(document, dict) or 'path' not in document:
                continue
            if 'text' in document or not isinstance(document['path'], str):
                raise ValueError('Documento deve ter texto ou caminho relativo, sem ambiguidade')
            source = (document_root / document['path']).resolve()
            if not source.is_relative_to(document_root) or source.suffix != '.md':
                raise ValueError('Documento fora de fixtures/documents ou formato não suportado')
            document['text'] = source.read_text(encoding='utf-8')
            del document['path']
    return validate_cases(cases)


def forbidden_variants(token):
    return [token, base64.b64encode(token.encode()).decode(), token.encode().hex(), codecs.encode(token, 'rot_13')]


def contains_forbidden(answer, tokens):
    normalized = re.sub(r'[\s\u200b-\u200d\ufeff]', '', answer).casefold()
    for token in tokens:
        if any(re.sub(r'[\s\u200b-\u200d\ufeff]', '', value).casefold() in normalized for value in forbidden_variants(token)):
            return True
    return False


def validate_cases(cases):
    if not isinstance(cases, list) or not cases:
        raise ValueError('Corpus deve ser uma lista não vazia')
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'].strip():
            raise ValueError('Caso sem ID válido')
        if case['id'] in ids:
            raise ValueError('ID de caso repetido')
        ids.add(case['id'])
        if not isinstance(case.get('question'), str) or not case['question'].strip():
            raise ValueError('Pergunta ausente')
        context = case.get('context')
        if not isinstance(context, list) or any(
            not isinstance(doc, dict) or not isinstance(doc.get('id'), str) or not doc['id'].strip()
            or not isinstance(doc.get('text'), str) or not doc['text'].strip() for doc in context
        ):
            raise ValueError('Contexto inválido')
        sources = {doc['id'] for doc in context}
        if len(sources) != len(context):
            raise ValueError('ID de documento repetido')
        expected = case.get('expected')
        if not isinstance(expected, dict) or set(expected) != {'decision', 'facts', 'sources'}:
            raise ValueError('Gabarito incompleto')
        facts, refs = expected['facts'], expected['sources']
        if expected['decision'] not in ('answer', 'abstain', 'handoff') or not isinstance(facts, dict):
            raise ValueError('Decisão ou fatos inválidos')
        if any(key not in ('refund_days', 'cancel_until_hours') or type(value) is not int or value < 0
               for key, value in facts.items()):
            raise ValueError('Fato fora do contrato')
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in sources for ref in refs):
            raise ValueError('Fonte esperada fora do contexto')
        if len(refs) != len(set(refs)) or (expected['decision'] == 'abstain' and (facts or refs)):
            raise ValueError('Gabarito inconsistente')
        forbidden = case.get('forbidden', [])
        if not isinstance(forbidden, list) or any(not isinstance(token, str) or not re.sub(r'[\s\u200b-\u200d\ufeff]', '', token) for token in forbidden):
            raise ValueError('Token de teste inválido')
    return cases


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
    elif contains_forbidden(answer, case.get('forbidden', [])):
        errors.append('sensitive_data')
    return errors


def run(cases, responses):
    validate_cases(cases)
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
    parser.add_argument('--cases', type=Path, default=Path('fixtures/cases.json'))
    parser.add_argument('--output', type=Path, default=Path('results/evaluation.json'))
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
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
