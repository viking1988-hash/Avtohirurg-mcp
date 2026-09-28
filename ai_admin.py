import json
from datetime import datetime, timezone

WORKFLOW_STATES = (
    "NEW", "INTAKE", "DIAGNOSIS", "APPROVAL", "BOOKED",
    "REPAIR", "COMPLETED", "REVIEW", "CONTENT", "RETURN",
)

WORKFLOW_TRANSITIONS = {
    "NEW": {"INTAKE"},
    "INTAKE": {"DIAGNOSIS"},
    "DIAGNOSIS": {"APPROVAL", "INTAKE"},
    "APPROVAL": {"BOOKED", "DIAGNOSIS"},
    "BOOKED": {"REPAIR", "INTAKE"},
    "REPAIR": {"COMPLETED", "APPROVAL"},
    "COMPLETED": {"REVIEW", "CONTENT", "RETURN"},
    "REVIEW": {"CONTENT", "RETURN"},
    "CONTENT": {"RETURN", "REVIEW"},
    "RETURN": {"INTAKE"},
}

def _normalize(value):
    return str(value or "").strip()

def next_workflow_state(current_state, event):
    current = _normalize(current_state).upper()
    action = _normalize(event).lower()
    events = {
        "new_lead": "INTAKE",
        "intake_complete": "DIAGNOSIS",
        "diagnosis_ready": "APPROVAL",
        "approval_granted": "BOOKED",
        "booking_confirmed": "REPAIR",
        "repair_started": "REPAIR",
        "repair_completed": "COMPLETED",
        "review_requested": "REVIEW",
        "content_ready": "CONTENT",
        "return_due": "RETURN",
        "new_visit": "INTAKE",
        "needs_more_data": "INTAKE",
        "diagnosis_rejected": "DIAGNOSIS",
        "booking_rejected": "DIAGNOSIS",
    }
    target = events.get(action)
    if current not in WORKFLOW_STATES:
        return {"status": "invalid", "current_state": current, "event": action}
    if target is None:
        return {
            "status": "unknown_event",
            "current_state": current,
            "event": action,
            "allowed_next_states": sorted(WORKFLOW_TRANSITIONS.get(current, set())),
        }
    allowed = WORKFLOW_TRANSITIONS.get(current, set())
    if target not in allowed:
        return {
            "status": "blocked",
            "current_state": current,
            "event": action,
            "target_state": target,
            "allowed_next_states": sorted(allowed),
            "rule": "Переход состояния выполняется только по разрешённому событию.",
        }
    return {"status": "ok", "current_state": current, "event": action, "next_state": target}

def build_diagnostic_card(
    car="", year="", mileage="", customer_symptom="", conditions="",
    facts=None, confirmed_faults=None, evidence=None, urgency="",
    deferred_items=None, repair="", result="", recommendations=None,
):
    facts = facts or []
    confirmed_faults = confirmed_faults or []
    evidence = evidence or []
    deferred_items = deferred_items or []
    recommendations = recommendations or []
    return {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "car": _normalize(car) or None,
        "year": _normalize(year) or None,
        "mileage": _normalize(mileage) or None,
        "customer_symptom": _normalize(customer_symptom) or None,
        "conditions": _normalize(conditions) or None,
        "facts": [str(x).strip() for x in facts if str(x).strip()],
        "confirmed_faults": [str(x).strip() for x in confirmed_faults if str(x).strip()],
        "evidence": [str(x).strip() for x in evidence if str(x).strip()],
        "urgency": _normalize(urgency) or None,
        "deferred_items": [str(x).strip() for x in deferred_items if str(x).strip()],
        "repair": _normalize(repair) or None,
        "result": _normalize(result) or None,
        "recommendations": [str(x).strip() for x in recommendations if str(x).strip()],
        "principle": "НЕ УГАДЫВАЕМ ДЕТАЛЬ. ДОКАЗЫВАЕМ НЕИСПРАВНОСТЬ.",
    }

def register_ai_admin_tools(mcp, log_tool, audit_log, action_policy, diagnostic_12_points, repair_urgency):
    def client_intake(name="", phone="", car="", year="", symptom="", preferred_time=""):
        """AUTO: принимает первичную заявку; не бронирует и не меняет CRM."""
        log_tool("client_intake", car=car, year=year, preferred_time=preferred_time)
        required={"Имя":name,"Телефон":phone,"Автомобиль":car,"Год":year,"Симптом":symptom}
        missing=[k for k,v in required.items() if not str(v).strip()]
        audit_log("client_intake","AUTO","prepared",fields_missing=len(missing))
        return json.dumps({
            "status":"ready_for_diagnostic" if not missing else "needs_more_data",
            "missing":missing, "client":name or None, "car":car or None,
            "year":year or None, "symptom":symptom or None,
            "preferred_time":preferred_time or None,
            "workflow_state":"DIAGNOSIS" if not missing else "INTAKE",
            "next_step":"diagnostic_plan" if not missing else "ask_missing_fields",
            "policy":action_policy("AUTO")
        },ensure_ascii=False,indent=2)

    def diagnostic_plan(car, symptom):
        """AUTO: строит диагностический маршрут по протоколу 12 пунктов."""
        log_tool("diagnostic_plan",car=car,symptom=symptom)
        plan=diagnostic_12_points(symptom=symptom,car=car)
        urgency=repair_urgency(symptom=symptom,car=car)
        audit_log("diagnostic_plan","AUTO","prepared",car=car)
        return plan+"\n\n"+urgency+"\n\nПолитика: "+action_policy("AUTO")

    def workflow_state(current_state, event):
        """AUTO: проверяет безопасный переход клиента между состояниями."""
        result = next_workflow_state(current_state, event)
        audit_log("workflow_state","AUTO",result.get("status","unknown"),current=current_state,event=event)
        return json.dumps({**result, "states": list(WORKFLOW_STATES), "policy": action_policy("AUTO")},
                          ensure_ascii=False, indent=2)

    def diagnostic_card(
        car="", year="", mileage="", customer_symptom="", conditions="",
        facts=None, confirmed_faults=None, evidence=None, urgency="",
        deferred_items=None, repair="", result="", recommendations=None,
    ):
        """AUTO: формирует диагностическую карту без выдуманных данных."""
        card = build_diagnostic_card(
            car, year, mileage, customer_symptom, conditions,
            facts, confirmed_faults, evidence, urgency,
            deferred_items, repair, result, recommendations
        )
        audit_log("diagnostic_card","AUTO","prepared",car=car,evidence_count=len(card["evidence"]))
        return json.dumps({"status":"ready","card":card,"policy":action_policy("AUTO")},
                          ensure_ascii=False, indent=2)

    def second_opinion(car, customer_symptom, external_diagnosis, evidence=""):
        """AUTO/APPROVAL: проверяет чужое заключение на наличие подтверждений."""
        log_tool("second_opinion",car=car)
        evidence_text=(evidence or "").strip()
        low=(external_diagnosis or "").lower()
        red_flags=["точно","100%","под замену","без диагностики","гарантированно"]
        status="requires_confirmation" if not evidence_text or any(x in low for x in red_flags) else "evidence_review"
        audit_log("second_opinion","AUTO","reviewed",evidence_present=bool(evidence_text))
        return json.dumps({
            "status":status, "car":car, "symptom":customer_symptom,
            "external_diagnosis":external_diagnosis, "evidence":evidence_text or None,
            "workflow_state":"APPROVAL",
            "rule":"Факт → проверка → результат → доказательство → решение",
            "policy":action_policy("APPROVAL")
        },ensure_ascii=False,indent=2)

    def booking_request(name, phone, car, service, preferred_date="", preferred_time=""):
        """APPROVAL: готовит заявку на запись; фактическую бронь не создаёт."""
        log_tool("booking_request",car=car,service=service,preferred_date=preferred_date,preferred_time=preferred_time)
        audit_log("booking_request","APPROVAL","prepared",service=service)
        return json.dumps({
            "status":"approval_required","name":name,"phone":phone,"car":car,"service":service,
            "preferred_date":preferred_date or None,"preferred_time":preferred_time or None,
            "workflow_state":"APPROVAL","policy":action_policy("APPROVAL")
        },ensure_ascii=False,indent=2)

    def content_case(car, problem, confirmed_fault, evidence, repair, result):
        """AUTO: превращает подтверждённый ремонт в контент-кейс без выдуманных фактов."""
        log_tool("content_case",car=car)
        audit_log("content_case","AUTO","prepared",evidence_present=bool(evidence))
        return json.dumps({
            "hook":f"Клиент приехал с проблемой: {problem}", "car":car,
            "confirmed_fault":confirmed_fault, "evidence":evidence,
            "repair":repair, "result":result, "cta":"Запись в Direct",
            "workflow_state":"CONTENT", "rule":"Использовать только подтверждённые факты.",
            "policy":action_policy("AUTO")
        },ensure_ascii=False,indent=2)

    def ai_admin_policy():
        """AUTO: возвращает матрицу прав AI-администратора."""
        log_tool("ai_admin_policy")
        audit_log("ai_admin_policy","AUTO","read")
        return json.dumps({
            "AUTO":["анализ","подготовка","структурирование","безопасное чтение"],
            "APPROVAL":["запись","публикация","изменение данных","внешние действия"],
            "HUMAN":["деньги","возвраты","изменение цен","критические решения","дорогие/спорные ремонты"],
            "workflow_states":list(WORKFLOW_STATES),
            "required":["audit_log","минимальные права","не логировать секреты","аварийная остановка"]
        },ensure_ascii=False,indent=2)

    mcp.tool()(client_intake)
    mcp.tool()(diagnostic_plan)
    mcp.tool()(workflow_state)
    mcp.tool()(diagnostic_card)
    mcp.tool()(second_opinion)
    mcp.tool()(booking_request)
    mcp.tool()(content_case)
    mcp.tool()(ai_admin_policy)
