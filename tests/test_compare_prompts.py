import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

import compare_prompts as compare


MODELS = ['first/model:free', 'second/model:free']
KEY = 'synthetic-api-credential-not-for-output'


def child_metadata(command):
    selected = json.loads(Path(command[command.index('--cases') + 1]).read_text(encoding='utf-8'))
    prompt = Path(command[command.index('--prompt-file') + 1]).read_text(encoding='utf-8') if '--prompt-file' in command else compare.SYSTEM
    return {'corpus_sha256': hashlib.sha256(json.dumps(selected, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, 'OUTPUT_DIR', tmp_path / 'results/comparison')
    monkeypatch.setattr(compare, 'load_dotenv', lambda: None)
    monkeypatch.setenv('OPENROUTER_API_KEY', KEY)
    monkeypatch.setenv('OPENROUTER_COMPARE_MODELS', ','.join(MODELS))
    cases = [{'id': name, 'split': split, 'question': 'Qual o prazo?',
              'context': [{'id': 'policy', 'text': 'Reembolso em 7 dias.'}],
              'expected': {'decision': 'answer', 'facts': {'refund_days': 7}, 'sources': ['policy']}}
             for name, split in [('dev-a', 'development'), ('dev-b', 'development'), ('holdout-a', 'holdout')]]
    source = tmp_path / 'cases.json'
    source.write_text(json.dumps(cases), encoding='utf-8')
    candidate = tmp_path / 'candidate.txt'
    candidate.write_text('Use apenas os documentos aplicáveis.', encoding='utf-8')
    calls = []

    def child(command, **kwargs):
        selected = json.loads(Path(command[command.index('--cases') + 1]).read_text(encoding='utf-8'))
        calls.append((command, selected, kwargs))
        output = Path(command[command.index('--output') + 1])
        output.write_text(json.dumps({**child_metadata(command), 'cases': [{'case_id': case['id'], 'errors': [],
                                                'response': {'answer': '7 dias'}} for case in selected]}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(compare.subprocess, 'run', child)
    monkeypatch.setattr(compare, 'preflight', lambda models, key: {'used': 0, 'limit': 100, 'remaining': 100})
    return SimpleNamespace(args=['--cases', str(source), '--candidate', str(candidate)], calls=calls,
                           child=child, output=compare.OUTPUT_DIR)


def report(comparison):
    return json.loads((comparison.output / 'comparison.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('remaining', [0, 7])
def test_insufficient_quota_preserves_report_without_generation(comparison, monkeypatch, remaining):
    monkeypatch.setattr(compare, 'preflight', lambda *args: {'used': 100 - remaining, 'limit': 100, 'remaining': remaining})
    assert compare.main(comparison.args) == 2
    assert comparison.calls == []
    assert report(comparison)['planned_requests'] == 8
    assert report(comparison)['stop_reason'] == 'daily_quota'
    assert (comparison.output / 'summary.md').is_file()


def test_budget_refusal_happens_before_network(comparison, monkeypatch):
    def unexpected(*args):
        pytest.fail('Budget inválido não deve acessar a API')
    monkeypatch.setattr(compare, 'preflight', unexpected)
    assert compare.main(comparison.args + ['--max-requests', '7']) == 2
    assert report(comparison)['stop_reason'] == 'request_budget'
    assert comparison.calls == []


@pytest.mark.parametrize(('split', 'ids'), [('development', ['dev-a', 'dev-b']), ('holdout', ['holdout-a']), ('all', ['dev-a', 'dev-b', 'holdout-a'])])
def test_paired_prompts_use_identical_cases_models_and_repeats(comparison, split, ids):
    assert compare.main(comparison.args + ['--split', split, '--repeats', '2']) == 0
    result = report(comparison)
    assert result['case_ids'] == ids
    assert len(result['runs']) == 8
    assert result['planned_requests'] == len(ids) * 8
    assert all([case['id'] for case in selected] == ids for _, selected, _ in comparison.calls)
    for model in MODELS:
        rows = [row for row in result['runs'] if row['model'] == model]
        assert [(row['prompt'], row['repeat']) for row in rows] == [('baseline', 1), ('candidate', 1), ('baseline', 2), ('candidate', 2)]
    assert all(kwargs['env']['OPENROUTER_BASE_URL'] == compare.API for _, _, kwargs in comparison.calls)
    assert all(KEY not in command for command, _, _ in comparison.calls)
    assert ['--prompt-file' in command for command, _, _ in comparison.calls] == [False, True] * 4


def test_quality_failures_are_preserved_and_other_pairs_continue(comparison, monkeypatch):
    def child(command, **kwargs):
        result = comparison.child(command, **kwargs)
        if '--prompt-file' not in command:
            output = Path(command[command.index('--output') + 1])
            data = json.loads(output.read_text(encoding='utf-8'))
            data['cases'][0]['errors'] = ['facts', 'sources']
            output.write_text(json.dumps(data))
            result.returncode = 1
        return result
    monkeypatch.setattr(compare.subprocess, 'run', child)
    assert compare.main(comparison.args) == 1
    result = report(comparison)
    assert result['status'] == 'quality_failure'
    assert len(result['runs']) == 4
    assert [row['failures'] for row in result['runs']] == [1, 0, 1, 0]
    first = json.loads((comparison.output / result['runs'][0]['report']).read_text(encoding='utf-8'))
    assert first['cases'][0]['errors'] == ['facts', 'sources']


@pytest.mark.parametrize('label', ['truncated', 'finish_reason', 'invalid_json', 'invalid_response'])
def test_generation_failure_is_explicit_without_stopping_candidate(comparison, monkeypatch, label):
    def child(command, **kwargs):
        result = comparison.child(command, **kwargs)
        if '--prompt-file' not in command:
            output = Path(command[command.index('--output') + 1])
            data = json.loads(output.read_text(encoding='utf-8'))
            data['cases'][0]['errors'] = [label]
            output.write_text(json.dumps(data))
            result.returncode = 1
        return result
    monkeypatch.setattr(compare.subprocess, 'run', child)
    assert compare.main(comparison.args) == 1
    result = report(comparison)
    assert len(result['runs']) == 4
    assert result['runs'][0]['error_counts'] == {label: 1}
    assert result['runs'][0]['errors'] == 0
    assert result['runs'][0]['interrupted'] is False
    assert result['runs'][1]['passed'] == 2
    summary = (comparison.output / 'summary.md').read_text(encoding='utf-8')
    assert f'{label}: 1' in summary
    assert 'não foi avaliado pelo contrato' in summary


@pytest.mark.parametrize('field', ['corpus_sha256', 'prompt_sha256'])
@pytest.mark.parametrize('value', [None, 'different-hash'])
def test_missing_or_different_fingerprint_stops_comparison(comparison, monkeypatch, field, value):
    def child(command, **kwargs):
        result = comparison.child(command, **kwargs)
        output = Path(command[command.index('--output') + 1])
        data = json.loads(output.read_text(encoding='utf-8'))
        data[field] = value
        output.write_text(json.dumps(data))
        return result
    monkeypatch.setattr(compare.subprocess, 'run', child)
    assert compare.main(comparison.args) == 2
    assert len(comparison.calls) == 1
    assert report(comparison)['status'] == 'setup_error'


@pytest.mark.parametrize('label', ['http_429', 'http_503', 'URLError', 'TimeoutError'])
def test_transport_failure_stops_batch_and_redacts_credential(comparison, monkeypatch, label, capsys):
    def child(command, **kwargs):
        comparison.calls.append(command)
        output = Path(command[command.index('--output') + 1])
        output.write_text(json.dumps({**child_metadata(command), 'cases': [{'case_id': 'dev-a', 'errors': [label], 'message': KEY}]}))
        return SimpleNamespace(returncode=1, stdout=KEY, stderr=KEY)
    monkeypatch.setattr(compare.subprocess, 'run', child)
    assert compare.main(comparison.args) == 2
    assert len(comparison.calls) == 1
    result = report(comparison)
    assert result['runs'][0]['errors'] == 1
    assert result['runs'][0]['passed'] == 0
    assert result['status'] == 'interrupted'
    assert KEY not in capsys.readouterr().out
    assert all(KEY not in file.read_text(encoding='utf-8') for file in comparison.output.rglob('*') if file.is_file())


def test_wrong_report_case_ids_cannot_pass(comparison, monkeypatch):
    def child(command, **kwargs):
        output = Path(command[command.index('--output') + 1])
        output.write_text(json.dumps({**child_metadata(command), 'cases': [{'case_id': 'not-requested', 'errors': []}]}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(compare.subprocess, 'run', child)
    assert compare.main(comparison.args) == 2
    assert report(comparison)['status'] == 'setup_error'


def test_timeout_keeps_partial_report(comparison, monkeypatch):
    def child(command, **kwargs):
        output = Path(command[command.index('--output') + 1])
        output.write_text(json.dumps({**child_metadata(command), 'cases': [{'case_id': 'dev-a', 'errors': []}]}))
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(compare.subprocess, 'run', child)
    assert compare.main(comparison.args) == 2
    row = report(comparison)['runs'][0]
    assert row['completed'] == 1 and row['expected'] == 2


def test_failing_child_without_reported_failures_cannot_pass(comparison, monkeypatch):
    def child(command, **kwargs):
        result = comparison.child(command, **kwargs)
        result.returncode = 1
        return result
    monkeypatch.setattr(compare.subprocess, 'run', child)
    assert compare.main(comparison.args) == 2
    assert len(comparison.calls) == 1


def test_resolved_documents_are_identical_for_both_prompts(comparison):
    source = Path(comparison.args[1])
    data = json.loads(source.read_text(encoding='utf-8'))
    documents = source.parent / 'documents'
    documents.mkdir()
    (documents / 'refund.md').write_text('Reembolso válido em sete dias.', encoding='utf-8')
    data[0]['context'] = [{'id': 'policy', 'path': 'refund.md'}]
    source.write_text(json.dumps(data), encoding='utf-8')
    assert compare.main(comparison.args) == 0
    for _, selected, _ in comparison.calls:
        assert selected[0]['context'] == [{'id': 'policy', 'text': 'Reembolso válido em sete dias.'}]


def catalog_entry():
    return {'id': MODELS[0], 'pricing': {'prompt': '0', 'completion': '0'},
            'supported_parameters': ['structured_outputs', 'response_format', 'reasoning']}


def test_preflight_extracts_only_numeric_daily_quota(monkeypatch):
    calls = []
    def request(target, *args):
        calls.append(target)
        if isinstance(target, str):
            return {'data': [catalog_entry()]}
        assert target.full_url == compare.API + '/key'
        assert target.get_header('Authorization') == 'Bearer ' + KEY
        return {'data': {'label': KEY, 'key': KEY, 'usage': 50,
                         'free_model_daily_requests': {'used': 5, 'limit': 50, 'remaining': 45, 'label': KEY}}}
    monkeypatch.setattr(compare, 'request_json', request)
    assert compare.preflight([MODELS[0]], KEY) == {'used': 5, 'limit': 50, 'remaining': 45}
    assert len(calls) == 2


@pytest.mark.parametrize('quota', [None, {}, {'remaining': 20}, {'used': 0, 'limit': 50, 'remaining': True}, {'used': 0, 'limit': 50, 'remaining': -1}])
def test_unknown_or_invalid_quota_is_not_assumed_available(monkeypatch, quota):
    monkeypatch.setattr(compare, 'request_json', lambda target, *args: {'data': [catalog_entry()]} if isinstance(target, str)
                        else {'data': {'free_model_daily_requests': quota}})
    with pytest.raises(ValueError):
        compare.preflight([MODELS[0]], KEY)


@pytest.mark.parametrize('change', [{'pricing': {'prompt': '1', 'completion': '0'}}, {'supported_parameters': ['response_format']}, {'reasoning': {'mandatory': True}}])
def test_paid_or_incompatible_model_is_refused(monkeypatch, change):
    entry = {**catalog_entry(), **change}
    monkeypatch.setattr(compare, 'request_json', lambda *args: {'data': [entry]})
    with pytest.raises(ValueError):
        compare.preflight([MODELS[0]], KEY)


def test_preflight_http_error_does_not_leak_exception_text(comparison, monkeypatch, capsys):
    def preflight(*args):
        raise HTTPError(compare.API + '/key', 401, KEY, {}, None)
    monkeypatch.setattr(compare, 'preflight', preflight)
    assert compare.main(comparison.args) == 2
    assert report(comparison)['stop_reason'] == 'http_401'
    assert KEY not in (comparison.output / 'comparison.json').read_text(encoding='utf-8')
    assert KEY not in capsys.readouterr().out
