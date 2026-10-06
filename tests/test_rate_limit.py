from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

import live_openrouter as live


def rate_limit(code=429, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers['Retry-After'] = retry_after
    return HTTPError('https://openrouter.ai/api/v1/chat/completions', code, 'limited',
                     headers, BytesIO(b'{"error":{"message":"limited"}}'))


def test_429_recupera_com_backoff_e_jitter(monkeypatch):
    calls, waits = [], []
    failures = [rate_limit(), rate_limit()]

    def request(req, timeout):
        calls.append(req)
        if len(calls) <= 2:
            raise failures[len(calls) - 1]
        return BytesIO(b'{"ok":true}')

    monkeypatch.setattr(live, 'urlopen', request)
    monkeypatch.setattr(live.time, 'sleep', waits.append)
    monkeypatch.setattr(live.random, 'uniform', lambda low, high: 1)
    policy = {'max_retries': 3, 'base_delay_seconds': 5}
    budget, transport = {'remaining_seconds': 120}, {}
    assert live.request_json('request', 90, policy, budget, transport) == {'ok': True}
    assert calls == ['request'] * 3
    assert waits == [6, 11]
    assert budget['remaining_seconds'] == 103
    assert transport['attempts'] == 3
    assert [item['status'] for item in transport['retries']] == [429, 429]
    assert all(error.fp.closed for error in failures)


@pytest.mark.parametrize('code', [400, 401, 402, 403, 500, 503])
def test_nao_repete_outros_status_http(monkeypatch, code):
    error = rate_limit(code)

    def request(*args, **kwargs):
        raise error

    monkeypatch.setattr(live, 'urlopen', request)
    monkeypatch.setattr(live.time, 'sleep', lambda _: pytest.fail('Não deve esperar'))
    transport = {}
    with pytest.raises(HTTPError) as caught:
        live.request_json('request', 90, {'max_retries': 3, 'base_delay_seconds': 5},
                          {'remaining_seconds': 120}, transport)
    assert caught.value is error
    assert transport == {'attempts': 1, 'retries': []}


@pytest.mark.parametrize('header,expected', [('12', 12), ('0', 0), ('invalid', 6),
                                           ('-1', 6), ('nan', 6), ('inf', 6)])
def test_retry_after_segundos_e_header_invalido(monkeypatch, header, expected):
    monkeypatch.setattr(live.random, 'uniform', lambda low, high: 1)
    assert live.retry_delay(header, 0, 5) == expected


def test_retry_after_data_http():
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    assert 58 <= live.retry_delay(future, 0, 5) <= 60
    assert live.retry_delay('Mon, 01 Jan 2001 00:00:00 GMT', 0, 5) == 0


@pytest.mark.parametrize('max_retries,budget,header,attempts,waits,reason', [
    (3, 120, '10', 4, [10, 10, 10], 'retry_limit'),
    (0, 120, '10', 1, [], 'retry_limit'),
    (3, 120, '121', 1, [], 'wait_budget'),
    (3, 15, '10', 2, [10], 'wait_budget'),
])
def test_limites_encerram_sem_tentar_antes_do_prazo(monkeypatch, max_retries, budget, header, attempts, waits, reason):
    actual_waits = []

    def request(*args, **kwargs):
        raise rate_limit(retry_after=header)

    monkeypatch.setattr(live, 'urlopen', request)
    monkeypatch.setattr(live.time, 'sleep', actual_waits.append)
    transport = {}
    remaining = {'remaining_seconds': budget}
    with pytest.raises(HTTPError):
        live.request_json('request', 90, {'max_retries': max_retries, 'base_delay_seconds': 5}, remaining, transport)
    assert transport['attempts'] == attempts
    assert transport['stop_reason'] == reason
    assert actual_waits == waits
    assert remaining['remaining_seconds'] == budget - sum(waits)


def test_live_registra_retry_sem_repetir_falha_de_contrato(tmp_path, monkeypatch):
    cases = json.loads(Path('fixtures/cases.json').read_text(encoding='utf-8'))[:2]
    responses = json.loads(Path('fixtures/responses.json').read_text(encoding='utf-8'))[:2]
    responses[1]['decision'] = 'answer'
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'fixtures').mkdir()
    (tmp_path / 'fixtures/cases.json').write_text(json.dumps(cases), encoding='utf-8')
    monkeypatch.setattr(live, 'load_dotenv', lambda: None)
    for key, value in {'OPENROUTER_BASE_URL': 'https://openrouter.ai/api/v1',
                       'OPENROUTER_API_KEY': 'synthetic-test-value', 'OPENROUTER_MAX_RETRIES': '3',
                       'OPENROUTER_RETRY_BASE_SECONDS': '5', 'OPENROUTER_RETRY_BUDGET_SECONDS': '120'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr('sys.argv', ['live_openrouter.py', '--model', 'test:free'])
    calls, waits = [], []

    def request(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            data = {'data': [{'id': 'test:free', 'pricing': {'prompt': '0', 'completion': '0'}}]}
        elif len(calls) == 2:
            raise rate_limit(retry_after='7')
        else:
            data = {'choices': [{'message': {'content': json.dumps(responses[len(calls) - 3])},
                                 'finish_reason': 'stop'}]}
        return BytesIO(json.dumps(data).encode())

    monkeypatch.setattr(live, 'urlopen', request)
    monkeypatch.setattr(live.time, 'sleep', waits.append)
    assert live.main() == 1
    report = json.loads((tmp_path / 'results/live.json').read_text(encoding='utf-8'))
    assert len(calls) == 4 and waits == [7]
    assert report['cases'][0]['transport']['attempts'] == 2
    assert report['cases'][0]['errors'] == []
    assert report['cases'][1]['transport']['attempts'] == 1
    assert report['cases'][1]['errors'] == ['decision']
    assert report['retry_budget']['remaining_seconds'] == 113
