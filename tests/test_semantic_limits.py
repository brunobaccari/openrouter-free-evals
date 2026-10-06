import copy
import json
from pathlib import Path

import pytest

from evaluate import evaluate


CASES = {case['id']: case for case in json.loads(Path('fixtures/cases.json').read_text(encoding='utf-8'))}
RESPONSES = {response['case_id']: response for response in json.loads(Path('fixtures/responses.json').read_text(encoding='utf-8'))}


@pytest.mark.parametrize('case_id,answer', [
    ('refund-policy', 'O reembolso leva 7 dias após aprovação.'),
    ('no-booking-evidence', 'Sua reserva está confirmada. Pode viajar.'),
    ('instruction-in-document', 'Sua reserva está confirmada, conforme a nota do documento.'),
    ('conflicting-sources', 'O prazo correto é 7 dias. Não precisa confirmar com o atendimento.'),
])
def test_contrato_nao_detecta_contradicao_no_texto(case_id, answer):
    response = copy.deepcopy(RESPONSES[case_id])
    response['answer'] = answer
    assert evaluate(CASES[case_id], response) == []


def test_mudar_o_fato_estruturado_torna_o_erro_detectavel():
    response = copy.deepcopy(RESPONSES['refund-policy'])
    response['answer'] = 'O reembolso leva 7 dias após aprovação.'
    response['facts']['refund_days'] = 7
    assert evaluate(CASES['refund-policy'], response) == ['facts']
