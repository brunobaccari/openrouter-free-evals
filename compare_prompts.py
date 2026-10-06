"""Comparação pareada, sem alterar o gabarito ou trocar casos entre prompts."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request
from uuid import uuid4

from dotenv import load_dotenv
from evaluate import load_cases
from live_openrouter import SYSTEM, request_json, require_free


API = 'https://openrouter.ai/api/v1'
OUTPUT_DIR = Path('results/comparison')
DEFAULT_MODEL = 'apodex/apodex-1.1-mini:free'
TRANSPORT_ERRORS = {'URLError', 'TimeoutError', 'ConnectionError', 'OSError'}


def preflight(models, key):
    policy = {'max_retries': 1, 'base_delay_seconds': 1, 'wait_budget_seconds': 10}
    budget = {'remaining_seconds': 10}
    catalog = request_json(API + '/models', 30, policy, budget, {})['data']
    if not isinstance(catalog, list) or any(not isinstance(item, dict) for item in catalog):
        raise ValueError('Catálogo inválido')
    for name in models:
        model = next((item for item in catalog if item.get('id') == name), None)
        if model is None:
            raise ValueError('Modelo ausente do catálogo')
        require_free(model)
        if not {'structured_outputs', 'response_format', 'reasoning'} <= set(model.get('supported_parameters', [])):
            raise ValueError('Modelo sem suporte ao contrato')
        if model.get('reasoning', {}).get('mandatory'):
            raise ValueError('Modelo exige raciocínio')
    request = Request(API + '/key', headers={'Authorization': f'Bearer {key}'})
    data = request_json(request, 30, policy, budget, {})['data']
    quota = data.get('free_model_daily_requests', {})
    if not isinstance(quota, dict) or any(type(quota.get(field)) is not int or quota[field] < 0
                                          for field in ('used', 'limit', 'remaining')):
        raise ValueError('Cota diária não disponível')
    return {field: quota[field] for field in ('used', 'limit', 'remaining')}


def summarize_run(data, expected_ids, corpus_hash, prompt_hash):
    if not isinstance(data, dict):
        raise ValueError('Relatório individual inválido')
    if data.get('corpus_sha256') != corpus_hash or data.get('prompt_sha256') != prompt_hash:
        raise ValueError('Corpus ou prompt diferente do lote planejado')
    items = data.get('cases')
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('Relatório individual inválido')
    ids = [item.get('case_id') for item in items]
    if ids != expected_ids[:len(ids)] or len(ids) > len(expected_ids):
        raise ValueError('Relatório com casos diferentes do corpus selecionado')
    failures = errors = 0
    error_counts = Counter()
    for item in items:
        labels = item.get('errors')
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise ValueError('Erros de caso inválidos')
        transport = any(label.startswith('http_') or label in TRANSPORT_ERRORS for label in labels)
        error_counts.update(set(labels))
        errors += int(transport)
        failures += int(bool(labels) and not transport)
    return {'expected': len(expected_ids), 'completed': len(items) - errors,
            'passed': len(items) - errors - failures, 'failures': failures, 'errors': errors,
            'error_counts': dict(sorted(error_counts.items())),
            'interrupted': bool(errors or data.get('setup_error') or len(items) != len(expected_ids))}


def save(report, key):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if key:
        text = text.replace(key, '[redacted]')
    (OUTPUT_DIR / 'comparison.json').write_text(text, encoding='utf-8')
    lines = ['## Comparação de prompts', '', f"Estado: **{report['status']}**.",
             f"Chamadas de geração planejadas: **{report.get('planned_requests', 0)}** (sem retries).", '']
    quota = report.get('free_model_daily_requests')
    if quota:
        lines += [f"Cota diária observada antes do lote: {quota['remaining']} disponíveis de {quota['limit']}.", '']
    if report.get('stop_reason'):
        lines += [f"Interrupção: `{report['stop_reason']}`. Lote sem execução completa não representa aprovação.", '']
    lines += ['| Modelo | Prompt | Repetição | Completos/previstos | Passaram | Falhas de resposta | Erros de transporte | Códigos (casos) |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for row in report['runs']:
        codes = ', '.join(f'{code}: {count}' for code, count in row['error_counts'].items()) or '—'
        lines.append(f"| {row['model']} | {row['prompt']} | {row['repeat']} | {row['completed']}/{row['expected']} | {row['passed']} | {row['failures']} | {row['errors']} | {codes} |")
    lines += ['', '`truncated`, `finish_reason`, `invalid_json` e `invalid_response` indicam falhas de geração/formato; o conteúdo desses casos não foi avaliado pelo contrato. Não são erros de transporte e não interrompem os outros pares.',
              'Os demais códigos de resposta registram violações do contrato. Um caso pode ter vários códigos. Completos são retornos recebidos, inclusive respostas inválidas; não significa aprovação.',
              'IDs, documentos, gabaritos e hashes de corpus/prompt são conferidos em cada execução.',
              'Uma amostra pequena não estabelece superioridade de modelo. O holdout deve permanecer fora da iteração do prompt.',
              'A cota é compartilhada e pode mudar após a consulta. Retries são adicionais e limitados pelo cliente live.']
    summary = '\n'.join(lines) + '\n'
    if key:
        summary = summary.replace(key, '[redacted]')
    (OUTPUT_DIR / 'summary.md').write_text(summary, encoding='utf-8')


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', default=[value.strip() for value in os.getenv('OPENROUTER_COMPARE_MODELS', DEFAULT_MODEL).split(',') if value.strip()])
    parser.add_argument('--cases', type=Path, default=Path('fixtures/adversarial.json'))
    parser.add_argument('--candidate', type=Path, default=Path('prompts/guardrails-v2.txt'))
    parser.add_argument('--split', choices=['development', 'holdout', 'all'], default='development')
    parser.add_argument('--repeats', type=int, choices=[1, 2, 3], default=1)
    parser.add_argument('--max-requests', type=int, default=60)
    args = parser.parse_args(argv)
    key = os.getenv('OPENROUTER_API_KEY', '')
    report = {'executed_at': datetime.now(timezone.utc).isoformat(), 'status': 'setup_error',
              'split': args.split, 'max_requests': args.max_requests, 'runs': []}
    try:
        if not key.strip():
            raise ValueError('OPENROUTER_API_KEY ausente')
        if not args.models or len(args.models) != len(set(args.models)) or any(
            not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+:free', model) for model in args.models
        ):
            raise ValueError('Modelos inválidos ou repetidos')
        cases = load_cases(args.cases)
        cases = [case for case in cases if args.split == 'all' or case.get('split') == args.split]
        if not cases:
            raise ValueError('Split sem casos')
        for case in cases:
            for document in case['context']:
                document.pop('path', None)
        candidate = args.candidate.read_text(encoding='utf-8')
        if not candidate.strip():
            raise ValueError('Prompt candidato vazio')
        planned = len(cases) * len(args.models) * args.repeats * 2
        report.update(planned_requests=planned, case_ids=[case['id'] for case in cases],
                      models=args.models, repeats=args.repeats,
                      corpus_sha256=hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                      prompt_sha256={name: hashlib.sha256(text.encode()).hexdigest()
                                     for name, text in [('baseline', SYSTEM), ('candidate', candidate)]})
        if args.max_requests < 1 or planned > args.max_requests:
            report.update(status='blocked', stop_reason='request_budget')
            return 2
        quota = preflight(args.models, key)
        report['free_model_daily_requests'] = quota
        if quota['remaining'] < planned:
            report.update(status='blocked', stop_reason='daily_quota')
            return 2
        directory = OUTPUT_DIR / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8])
        directory.mkdir(parents=True)
        selected = directory / 'selected-cases.json'
        selected.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
        prompt_file = directory / 'candidate.txt'
        prompt_file.write_text(candidate, encoding='utf-8')
        report['status'] = 'running'
        save(report, key)
        for model_index, model in enumerate(args.models):
            for repeat in range(1, args.repeats + 1):
                for prompt in ['baseline', 'candidate']:
                    output = directory / f'model-{model_index + 1}-{prompt}-{repeat}.json'
                    command = [sys.executable, str(Path(__file__).with_name('live_openrouter.py')),
                               '--model', model, '--cases', str(selected.resolve()), '--output', str(output.resolve())]
                    if prompt == 'candidate':
                        command += ['--prompt-file', str(prompt_file.resolve())]
                    env = dict(os.environ, OPENROUTER_BASE_URL=API)
                    try:
                        result = subprocess.run(command, env=env, capture_output=True, text=True,
                                                timeout=300 + 360 * len(cases), check=False)
                        returncode = result.returncode
                    except subprocess.TimeoutExpired:
                        returncode = 2
                    data = json.loads(output.read_text(encoding='utf-8')) if output.exists() else {'cases': [], 'setup_error': 'missing_report'}
                    output.write_text(json.dumps(data, ensure_ascii=False, indent=2).replace(key, '[redacted]'), encoding='utf-8')
                    row = {'model': model, 'prompt': prompt, 'repeat': repeat,
                           'report': str(output.relative_to(OUTPUT_DIR)),
                           **summarize_run(data, report['case_ids'], report['corpus_sha256'], report['prompt_sha256'][prompt])}
                    report['runs'].append(row)
                    save(report, key)
                    if row['interrupted'] or returncode not in (0, 1) or (returncode == 1 and not row['failures']):
                        report.update(status='interrupted', stop_reason='live_setup_or_transport')
                        return 2
        report['status'] = 'quality_failure' if any(row['failures'] for row in report['runs']) else 'passed'
        return 1 if report['status'] == 'quality_failure' else 0
    except (ValueError, KeyError, TypeError, ArithmeticError, OSError, URLError) as error:
        report.update(status='setup_error', stop_reason=f'http_{error.code}' if isinstance(error, HTTPError) else type(error).__name__)
        return 2
    finally:
        save(report, key)
        print(f"Comparação: {report['status']}; relatório em results/comparison/.")


if __name__ == '__main__':
    raise SystemExit(main())
