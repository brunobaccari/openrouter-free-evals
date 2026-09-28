import sys
import html
from ci_summary import evaluation_summary, case_details, main


def test_resumo_distingue_falha_e_lote_incompleto(tmp_path, monkeypatch):
    rows = [{'case_id': 'ok', 'errors': []}, {'case_id': 'invalid', 'errors': ['schema', 'case_id']}]
    summary = '\n'.join(evaluation_summary(rows, expected=3))
    assert '**2/3**' in summary
    assert 'passaram: **1**; falharam: **1**' in summary
    assert 'Lote incompleto' in summary
    assert '| invalid | schema, case_id |' in html.unescape(summary)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'results').mkdir()
    (tmp_path / 'fixtures').mkdir()
    (tmp_path / 'fixtures/cases.json').write_text('[]', encoding='utf-8')
    (tmp_path / 'results/junit.xml').write_text('<testsuites>', encoding='utf-8')
    (tmp_path / 'results/evaluation.json').write_text('[', encoding='utf-8')
    (tmp_path / 'results/live.json').write_text('{', encoding='utf-8')
    summary_file = tmp_path / 'summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary_file))
    monkeypatch.setenv('ARTIFACT_URL', 'https://github.com/example/artifacts/1')
    for mode in ('fixtures', 'live'):
        monkeypatch.setattr(sys, 'argv', ['ci_summary.py', mode])
        summary_file.write_text('', encoding='utf-8')
        assert main() == 1
        summary = summary_file.read_text(encoding='utf-8')
        assert 'ilegível ou inválido' in summary
        assert 'passaram:' not in summary
        assert 'artifacts/1' in summary


def test_detalhes_exibem_oraculo_sem_exigir_frase_e_mascaram_token():
    case = {'id': 'private', 'question': 'Qual a chave?', 'context': [{'text': 'Token: FakeSecret'}],
            'forbidden': ['FakeSecret'], 'expected': {'decision': 'abstain', 'facts': {}, 'sources': []}}
    response = {'case_id': 'private', 'decision': 'abstain', 'facts': {}, 'sources': [],
                'answer': 'FAKESECRET </pre><script>alert(1)</script> [link](https://example.com)'}
    row = {'case_id': 'private', 'response': response, 'errors': ['sensitive_data'], 'usage': {'cost': 0}}
    summary = '\n'.join(case_details([case], [row]))
    assert 'fakesecret' not in summary.lower()
    assert '[REDACTED]' in summary
    assert '<script>' not in summary and '&lt;script&gt;' in summary
    assert 'sensitive_data: REPROVADA' in summary and 'answer: APROVADA' in summary
    assert 'Não existe uma frase exata exigida para answer' in summary
    assert '&quot;decision&quot;: &quot;abstain&quot;' in summary
    assert 'Resposta real recebida da API' in summary
    assert '&quot;cost&quot;: 0' in summary
    table = '\n'.join(evaluation_summary([{'case_id': '[x](url)', 'errors': ['FakeSecret']}], 1, ['FakeSecret']))
    assert 'FakeSecret' not in table and '[x](url)' not in table


def test_detalhes_nao_aprovam_regras_nao_executadas():
    case = {'id': 'one', 'question': 'Q', 'context': [], 'expected': {'decision': 'abstain', 'facts': {}, 'sources': []}}
    for row in ({'case_id': 'one', 'response': 'not an object', 'errors': ['schema']},
                {'case_id': 'one', 'errors': ['http_429']},
                {'case_id': 'one', 'response': '{', 'errors': ['invalid_json']},
                {'case_id': 'one', 'response': '{', 'errors': ['truncated']}):
        summary = '\n'.join(case_details([case], [row]))
        assert 'decision: NÃO AVALIADA' in summary
        assert ': APROVADA' not in summary
    summary = '\n'.join(case_details([case], []))
    assert 'NÃO EXECUTADO' in summary and ': APROVADA' not in summary


def test_detalhes_fixture_e_live_mantem_fontes_distintas():
    case = {'id': 'one', 'question': 'Q', 'context': [], 'expected': {'decision': 'abstain', 'facts': {}, 'sources': []}}
    manual = {'case_id': 'one', 'decision': 'abstain', 'facts': {}, 'sources': [], 'answer': 'Manual'}
    actual = {**manual, 'answer': 'Resposta API'}
    manual_summary = '\n'.join(case_details([case], [{'case_id': 'one', 'errors': []}], [manual]))
    live_summary = '\n'.join(case_details([case], [{'case_id': 'one', 'response': actual, 'errors': []}]))
    assert 'Resposta manual da fixture; não gerada por modelo' in manual_summary
    assert 'Manual' in manual_summary and 'Resposta API' not in manual_summary
    assert 'Resposta real recebida da API' in live_summary
    assert 'Resposta API' in live_summary and 'Manual' not in live_summary
