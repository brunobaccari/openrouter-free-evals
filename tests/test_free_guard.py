import json
import pytest
from live_openrouter import require_free, payload, main


@pytest.mark.parametrize('model', [
    {'id': 'paid-model', 'pricing': {'prompt': '0', 'completion': '0'}},
    {'id': 'model:free', 'pricing': {'prompt': '0.001', 'completion': '0'}},
    {'id': 'model:free', 'pricing': {'prompt': '0', 'completion': '0', 'request': '0.01'}},
    {'id': 'model:free', 'pricing': {}},
])
def test_recusa_modelo_pago_ou_preco_incompleto(model):
    with pytest.raises(ValueError):
        require_free(model)


def test_aceita_modelo_gratuito():
    require_free({'id': 'model:free', 'pricing': {'prompt': '0', 'completion': '0'}})


def test_nao_envia_gabarito_e_bloqueia_fallback_pago():
    case = {'id': 'case', 'question': 'Pergunta', 'context': [], 'expected': {'answer': 'Gabarito'}}
    request = payload(case, 'model:free')
    assert 'expected' not in json.loads(request['messages'][1]['content'])
    assert request['provider']['allow_fallbacks'] is False
    assert request['provider']['max_price'] == {'prompt': 0, 'completion': 0, 'request': 0}
    assert request['provider']['require_parameters'] is True
    assert request['reasoning'] == {'enabled': False, 'exclude': True}
    assert request['response_format']['type'] == 'json_schema'
    schema = request['response_format']['json_schema']['schema']
    assert set(schema['required']) == {'case_id', 'decision', 'answer', 'facts', 'sources'}
    assert schema['additionalProperties'] is False


def test_schema_e_prompt_nao_mudam_com_o_gabarito():
    case = {'id': 'independent', 'question': 'Pergunta', 'context': [],
            'expected': {'decision': 'answer', 'facts': {'refund_days': 731}, 'sources': ['secret-oracle']}}
    first = payload(case, 'model:free')
    case['expected'] = {'decision': 'abstain', 'facts': {}, 'sources': []}
    assert payload(case, 'model:free') == first
    serialized = json.dumps(first)
    assert '731' not in serialized and 'secret-oracle' not in serialized


def test_recusa_destino_diferente_da_openrouter_antes_de_enviar_chave(monkeypatch):
    monkeypatch.setenv('OPENROUTER_BASE_URL', 'https://example.com/api/v1')
    monkeypatch.setenv('OPENROUTER_MODEL', 'model:free')
    monkeypatch.setattr('sys.argv', ['live_openrouter.py'])

    def unexpected_request(*args, **kwargs):
        pytest.fail('Não deve consultar catálogo nem enviar credencial ao destino inválido')

    monkeypatch.setattr('live_openrouter.urlopen', unexpected_request)
    assert main() == 2
