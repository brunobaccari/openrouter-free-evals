import copy
import json
from pathlib import Path
import subprocess
import sys
import pytest
from evaluate import evaluate, run

CASES = json.loads(Path('fixtures/cases.json').read_text(encoding='utf-8'))
RESPONSES = json.loads(Path('fixtures/responses.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_cenario_manual_respeita_contrato(case):
    response = next(response for response in RESPONSES if response['case_id'] == case['id'])
    assert evaluate(case, response) == []


def test_corpus_completo_sem_ids_repetidos():
    assert len({case['id'] for case in CASES}) == len(CASES)
    assert all(not result['errors'] for result in run(CASES, RESPONSES))


@pytest.mark.parametrize('index,field,value,error', [
    (0, 'facts', {'refund_days': 7}, 'facts'),
    (0, 'facts', {'refund_days': True}, 'facts'),
    (0, 'sources', ['invented-url'], 'sources'),
    (0, 'sources', ['policy-v1', 'policy-v1'], 'sources'),
    (1, 'decision', 'answer', 'decision'),
    (2, 'facts', {'booking_status': 'confirmed'}, 'facts'),
    (3, 'decision', 'answer', 'decision'),
    (4, 'answer', 'Sua chave: QA-FAKE-SECRET-123', 'sensitive_data'),
    (0, 'answer', ' ', 'answer'),
    (0, 'sources', 'policy-v1', 'sources'),
    (0, 'case_id', 'other-case', 'case_id'),
])
def test_avaliador_detecta_mutacao_conhecida(index, field, value, error):
    response = copy.deepcopy(RESPONSES[index])
    response[field] = value
    assert error in evaluate(CASES[index], response)


@pytest.mark.parametrize('responses', [RESPONSES[:-1], RESPONSES + [RESPONSES[0]], [None], {}])
def test_nao_aprova_lote_incompleto_ou_invalido(responses):
    with pytest.raises(ValueError):
        run(CASES, responses)


def test_entrada_nao_objeto():
    assert evaluate(CASES[0], None) == ['schema']


def test_cli_reprova_resposta_incorreta(tmp_path):
    responses = copy.deepcopy(RESPONSES)
    responses[0]['facts']['refund_days'] = 3
    source = tmp_path / 'bad.json'
    output = tmp_path / 'report.json'
    source.write_text(json.dumps(responses), encoding='utf-8')
    result = subprocess.run([sys.executable, 'evaluate.py', '--responses', str(source), '--output', str(output)], capture_output=True)
    assert result.returncode == 1
    assert json.loads(output.read_text(encoding='utf-8'))[0]['errors'] == ['facts']
