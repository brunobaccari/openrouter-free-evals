import base64
import codecs
import json
from pathlib import Path

import pytest

from ci_summary import escaped, main as summary_main
from evaluate import evaluate, load_cases, run, contains_forbidden
from live_openrouter import payload


def test_documentos_alimentam_corpus_sem_enviar_gabarito():
    cases = load_cases('fixtures/adversarial.json')
    responses = json.loads(Path('fixtures/adversarial-responses.json').read_text(encoding='utf-8'))
    assert len(cases) == 12
    assert all(not row['errors'] for row in run(cases, responses))
    assert len([case for case in cases if case['split'] == 'holdout']) == 4
    request = payload(cases[0], 'test:free')
    user = json.loads(request['messages'][1]['content'])
    assert '14 dias' in user['context'][0]['text']
    assert 'expected' not in user and 'path' not in user['context'][0]


@pytest.mark.parametrize('path', ['../outside.md', '/outside.md', 'policy.txt'])
def test_documento_fora_da_pasta_permitida_e_recusado(tmp_path, path):
    cases = load_cases('fixtures/cases.json')[:1]
    cases[0]['context'] = [{'id': 'policy-v1', 'path': path}]
    source = tmp_path / 'cases.json'
    source.write_text(json.dumps(cases), encoding='utf-8')
    with pytest.raises(ValueError):
        load_cases(source)


@pytest.mark.parametrize('transform', [
    lambda token: token,
    lambda token: base64.b64encode(token.encode()).decode(),
    lambda token: token.encode().hex(),
    lambda token: codecs.encode(token, 'rot_13'),
    lambda token: ' '.join(token),
    lambda token: '\u200b'.join(token),
])
def test_token_transformado_reprova_e_e_mascarado_no_summary(transform):
    case = next(case for case in load_cases('fixtures/adversarial.json') if case['id'] == 'secret-base64')
    value = transform(case['forbidden'][0])
    response = {'case_id': case['id'], 'decision': 'abstain', 'facts': {}, 'sources': [], 'answer': value}
    assert evaluate(case, response) == ['sensitive_data']
    assert escaped(value, case['forbidden']) == '[REDACTED]'


def test_mascara_normaliza_token_com_espacos():
    assert escaped('SECRET123', ['SECRET 123']) == '[REDACTED]'
    assert contains_forbidden('SECRET123', ['SEC\u200bRET123'])
    assert escaped('SECRET123', ['SEC\u200bRET123']) == '[REDACTED]'


def test_summary_live_usa_o_corpus_escolhido(tmp_path, monkeypatch):
    cases = load_cases('fixtures/adversarial.json')
    responses = json.loads(Path('fixtures/adversarial-responses.json').read_text(encoding='utf-8'))
    rows = [{'case_id': case['id'], 'response': response, 'errors': evaluate(case, response)}
            for case, response in zip(cases, responses)]
    report = tmp_path / 'live.json'
    report.write_text(json.dumps({'cases': rows}), encoding='utf-8')
    summary = tmp_path / 'summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setattr('sys.argv', ['ci_summary.py', 'live', '--cases', 'fixtures/adversarial.json', '--report', str(report)])
    assert summary_main() == 0
    assert 'passaram: **12**' in summary.read_text(encoding='utf-8')
