import json
import unittest

from ai_admin import (
    WORKFLOW_STATES,
    build_diagnostic_card,
    build_return_loop,
    next_workflow_state,
)


class AiAdminV2Tests(unittest.TestCase):
    def test_happy_path_states(self):
        state = "NEW"
        for event, expected in [
            ("new_lead", "INTAKE"),
            ("intake_complete", "DIAGNOSIS"),
            ("diagnosis_ready", "APPROVAL"),
            ("approval_granted", "BOOKED"),
            ("booking_confirmed", "REPAIR"),
            ("repair_completed", "COMPLETED"),
            ("review_requested", "REVIEW"),
            ("content_ready", "CONTENT"),
            ("return_due", "RETURN"),
            ("new_visit", "INTAKE"),
        ]:
            result = next_workflow_state(state, event)
            self.assertEqual(result["status"], "ok")
            state = result["next_state"]
            self.assertEqual(state, expected)

    def test_invalid_transition_is_blocked(self):
        result = next_workflow_state("NEW", "approval_granted")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["target_state"], "BOOKED")

    def test_unknown_state_is_rejected(self):
        result = next_workflow_state("HACKED", "new_lead")
        self.assertEqual(result["status"], "invalid")

    def test_unknown_event_is_safe(self):
        result = next_workflow_state("DIAGNOSIS", "something_else")
        self.assertEqual(result["status"], "unknown_event")
        self.assertIn("APPROVAL", result["allowed_next_states"])

    def test_return_loop_starts_after_completion(self):
        result = build_return_loop("COMPLETED", next_due_date="2026-10-15", recommendations=["Проверить пыльник"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["next_state"], "RETURN")
        self.assertIn("запросить отзыв", result["steps"])
        self.assertEqual(result["next_due_date"], "2026-10-15")

    def test_return_loop_is_blocked_before_completion(self):
        result = build_return_loop("DIAGNOSIS")
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("next_state", result)

    def test_return_loop_does_not_promote_deferred_items(self):
        result = build_return_loop("COMPLETED", deferred_items=["Проверка задней подвески"])
        self.assertEqual(result["deferred_items"], ["Проверка задней подвески"])
        self.assertIn("Не выдумывать сроки", result["rule"])

    def test_diagnostic_card_preserves_only_supplied_facts(self):
        card = build_diagnostic_card(
            car="Toyota Camry",
            year="2015",
            mileage="120000",
            customer_symptom="Стучит спереди",
            facts=["Есть люфт"],
            confirmed_faults=["Рулевая тяга"],
            evidence=["Фото люфта"],
            urgency="attention",
            deferred_items=["Проверка задней подвески"],
            repair="Замена рулевой тяги",
            result="Люфт устранён",
        )
        self.assertEqual(card["car"], "Toyota Camry")
        self.assertEqual(card["confirmed_faults"], ["Рулевая тяга"])
        self.assertEqual(card["evidence"], ["Фото люфта"])
        self.assertEqual(card["deferred_items"], ["Проверка задней подвески"])
        self.assertEqual(card["principle"], "НЕ УГАДЫВАЕМ ДЕТАЛЬ. ДОКАЗЫВАЕМ НЕИСПРАВНОСТЬ.")
        json.dumps(card, ensure_ascii=False)
        self.assertEqual(len(WORKFLOW_STATES), 10)


if __name__ == "__main__":
    unittest.main()
