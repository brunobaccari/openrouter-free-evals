import sys
from ci_summary import evaluation_summary, main


def test_resumo_distingue_falha_e_lote_incompleto(tmp_path, monkeypatch):
    rows = [{'case_id': 'ok', 'errors': []}, {'case_id': 'invalid', 'errors': ['schema', 'case_id']}]
    summary = '\n'.join(evaluation_summary(rows, expected=3))
    assert '**2/3**' in summary
    assert 'passaram: **1**; falharam: **1**' in summary
    assert 'Lote incompleto' in summary
    assert '| invalid | schema, case_id |' in summary
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
