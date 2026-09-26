from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_core_contains_evidence_first_rules():
    text = (ROOT / "docs/JARVIS_CORE_PROMPT_RU.md").read_text(encoding="utf-8")
    assert "ФАКТЫ → ГИПОТЕЗЫ → ПРОВЕРКИ → ПОДТВЕРЖДЕНИЕ → ДЕЙСТВИЕ → ПРОВЕРКА РЕЗУЛЬТАТА." in text
    assert "НЕ УГАДЫВАЕМ ДЕТАЛЬ. ДОКАЗЫВАЕМ НЕИСПРАВНОСТЬ." in text
    assert "Не продвигать услуги автокондиционеров." in text


def test_regression_checklist_covers_required_smoke_cases():
    text = (ROOT / "docs/JARVIS_REGRESSION_CHECKLIST.md").read_text(encoding="utf-8")
    for item in ("diagnose_symptom", "repair_urgency", "diagnostic_12_points", "client_conclusion", "WordPress", "Toyota Camry 2015"):
        assert item in text


def test_runtime_integration_forbids_invented_gpt_configuration():
    text = (ROOT / "docs/JARVIS_RUNTIME_INTEGRATION.md").read_text(encoding="utf-8")
    assert "Do not duplicate or invent Custom GPT instructions" in text
    assert "do not deploy this migration branch to production until runtime smoke tests pass" in text


def test_server_exposes_core_rules_and_diagnostic_tools():
    text = (ROOT / "server.py").read_text(encoding="utf-8")
    for symbol in ("AVTOHIRURG_RULES", "diagnose_symptom", "repair_urgency", "diagnostic_12_points", "client_conclusion"):
        assert symbol in text
