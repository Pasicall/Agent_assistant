from types import SimpleNamespace


def test_numeric_verification_and_semantic_fail_closed(monkeypatch):
    from app.config import Settings
    from app.services import answering
    from app.services.verification import verify_answer
    settings = Settings(_env_file=None)
    sources = [{'text': '住宿报销每晚不超过500元。申请在30天内提交。'}]
    assert verify_answer('限额500元，30天内提交 [1]', sources, settings)['passed']
    assert not verify_answer('限额5000元 [1]', sources, settings)['passed']
    assert not verify_answer('1. 5000元 [1]', sources, settings)['passed']
    settings.citation_verifier = 'llm'
    monkeypatch.setattr(answering, 'configured_llm', lambda *a: SimpleNamespace(invoke=lambda _: SimpleNamespace(content='not json')))
    result = verify_answer('限额500元 [1]', sources, settings)
    assert not result['passed'] and result['issues'] == ['verifier_unavailable']
