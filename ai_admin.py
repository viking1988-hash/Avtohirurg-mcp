import json
from datetime import datetime, timezone

def register_ai_admin_tools(mcp, log_tool, audit_log, action_policy, diagnostic_12_points, repair_urgency):
    def client_intake(name="", phone="", car="", year="", symptom="", preferred_time=""):
        """AUTO: принимает первичную заявку; не бронирует и не меняет CRM."""
        log_tool("client_intake", car=car, year=year, preferred_time=preferred_time)
        required={"Имя":name,"Телефон":phone,"Автомобиль":car,"Год":year,"Симптом":symptom}
        missing=[k for k,v in required.items() if not str(v).strip()]
        audit_log("client_intake","AUTO","prepared",fields_missing=len(missing))
        return json.dumps({
            "status":"ready_for_diagnostic" if not missing else "needs_more_data",
            "missing":missing,
            "client":name or None,
            "car":car or None,
            "year":year or None,
            "symptom":symptom or None,
            "preferred_time":preferred_time or None,
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

    def second_opinion(car, customer_symptom, external_diagnosis, evidence=""):
        """AUTO/APPROVAL: проверяет чужое заключение на наличие подтверждений."""
        log_tool("second_opinion",car=car)
        evidence_text=(evidence or "").strip()
        low=(external_diagnosis or "").lower()
        red_flags=["точно","100%","под замену","без диагностики","гарантированно"]
        status="requires_confirmation" if not evidence_text or any(x in low for x in red_flags) else "evidence_review"
        audit_log("second_opinion","AUTO","reviewed",evidence_present=bool(evidence_text))
        return json.dumps({
            "status":status,
            "car":car,
            "symptom":customer_symptom,
            "external_diagnosis":external_diagnosis,
            "evidence":evidence_text or None,
            "rule":"Факт → проверка → результат → доказательство → решение",
            "policy":action_policy("APPROVAL")
        },ensure_ascii=False,indent=2)

    def booking_request(name, phone, car, service, preferred_date="", preferred_time=""):
        """APPROVAL: готовит заявку на запись; фактическую бронь не создаёт."""
        log_tool("booking_request",car=car,service=service,preferred_date=preferred_date,preferred_time=preferred_time)
        audit_log("booking_request","APPROVAL","prepared",service=service)
        return json.dumps({
            "status":"approval_required",
            "name":name,"phone":phone,"car":car,"service":service,
            "preferred_date":preferred_date or None,
            "preferred_time":preferred_time or None,
            "policy":action_policy("APPROVAL")
        },ensure_ascii=False,indent=2)

    def content_case(car, problem, confirmed_fault, evidence, repair, result):
        """AUTO: превращает подтверждённый ремонт в контент-кейс без выдуманных фактов."""
        log_tool("content_case",car=car)
        audit_log("content_case","AUTO","prepared",evidence_present=bool(evidence))
        return json.dumps({
            "hook":f"Клиент приехал с проблемой: {problem}",
            "car":car,
            "confirmed_fault":confirmed_fault,
            "evidence":evidence,
            "repair":repair,
            "result":result,
            "cta":"Запись в Direct",
            "rule":"Использовать только подтверждённые факты.",
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
            "required":["audit_log","минимальные права","не логировать секреты","аварийная остановка"]
        },ensure_ascii=False,indent=2)

    mcp.tool()(client_intake)
    mcp.tool()(diagnostic_plan)
    mcp.tool()(second_opinion)
    mcp.tool()(booking_request)
    mcp.tool()(content_case)
    mcp.tool()(ai_admin_policy)
