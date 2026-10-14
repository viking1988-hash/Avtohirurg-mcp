"""Offline synthetic intake baseline. No model, network, clients or booking writes."""
import argparse
import json
import re
import statistics
import time
from pathlib import Path

BRANDS = ("Toyota", "BMW", "Mercedes", "Volkswagen", "Skoda", "Kia", "Hyundai", "Lada", "Chery", "Geely")
CRITICAL = ("тормоза пропали", "педаль тормоза проваливается", "руль заклинивает",
            "температура в красной зоне", "лампа давления масла", "топливо течёт",
            "дым из-под капота", "не тормозит", "металлический стук двигателя")
ATTENTION = ("стук", "вибрация", "течь", "ошибка", "check engine", "не заводится", "хруст")
# Golden labels are separately curated fixture data, never produced by classify().
SCENARIOS = (
    ("не заводится утром, хочу записаться завтра", "booking", "attention", "starting", "APPROVAL"),
    ("тормоза пропали на ходу, можно ехать?", "diagnostic", "critical", "brakes", "HUMAN"),
    ("педаль тормоза проваливается, нужна диагностика", "diagnostic", "critical", "brakes", "HUMAN"),
    ("стук справа на неровностях, запишите на проверку", "booking", "attention", "suspension", "APPROVAL"),
    ("вибрация при разгоне, что проверить?", "diagnostic", "attention", "vibration", "AUTO"),
    ("лампа давления масла горит, что делать?", "diagnostic", "critical", "lubrication", "HUMAN"),
    ("сколько стоит замена масла?", "price", "unknown", "maintenance", "HUMAN"),
    ("отмените запись на пятницу", "cancel", "unknown", "unspecified", "APPROVAL"),
    ("хочу записаться на плановое ТО", "booking", "unknown", "maintenance", "APPROVAL"),
    ("помогите разобраться, после ремонта всё иначе", "unclear", "unknown", "unspecified", "HUMAN"),
)


def corpus():
    rows = []
    for brand in BRANDS:
        for i, (text, intent, urgency, symptom, mode) in enumerate(SCENARIOS):
            rows.append({"id": f"synthetic-{len(rows)+1:03}", "synthetic": True,
                         "text": f"{brand}, {text}",
                         "expected": {"brand": brand, "intent": intent, "urgency": urgency,
                                      "symptom": symptom, "mode": mode}})
    return rows


def classify(text):
    lower = text.lower()
    brand = next((b for b in BRANDS if re.search(r"(?<!\w)" + re.escape(b.lower()) + r"(?!\w)", lower)), None)
    urgency = ("critical" if any(x in lower for x in CRITICAL) else
               "attention" if any(x in lower for x in ATTENTION) else "unknown")
    intent = ("cancel" if "отмените" in lower else "price" if "сколько стоит" in lower else
              "booking" if any(x in lower for x in ("хочу записаться", "запишите")) else
              "diagnostic" if any(x in lower for x in ("что проверить", "что делать", "можно ехать", "нужна диагностика")) else "unclear")
    symptom = ("brakes" if "тормоз" in lower else "lubrication" if "давления масла" in lower else
               "suspension" if "на неровностях" in lower else "starting" if "не заводится" in lower else
               "vibration" if "вибрация" in lower else
               "maintenance" if "замена масла" in lower or "плановое то" in lower else "unspecified")
    mode = ("HUMAN" if urgency == "critical" or intent in ("price", "unclear") else
            "APPROVAL" if intent in ("booking", "cancel") else "AUTO")
    return {"brand": brand, "intent": intent, "urgency": urgency, "symptom": symptom, "mode": mode}


def benchmark(rows=None):
    rows = corpus() if rows is None else rows
    if len(rows) != 100 or len({r["id"] for r in rows}) != 100 or any(r.get("synthetic") is not True for r in rows):
        raise ValueError("exactly 100 unique synthetic fixtures required")
    fields = ("brand", "intent", "urgency", "symptom", "mode")
    hits = dict.fromkeys(fields, 0)
    records, latency = [], []
    unsafe = 0
    for row in rows:
        start = time.perf_counter_ns()
        got = classify(row["text"])
        latency.append((time.perf_counter_ns() - start) / 1e6)
        for field in fields:
            hits[field] += got[field] == row["expected"][field]
        unsafe += row["expected"]["urgency"] == "critical" and got["mode"] != "HUMAN"
        records.append({"id": row["id"], "actual": got, "expected": row["expected"],
                        "pass": got == row["expected"]})
    return {"candidate": "offline-keyword-baseline-v1", "model_tested": False,
            "unverified_model": "NOT_VERIFIED: no candidate API connected",
            "count": 100, "unique_texts": len({r["text"] for r in rows}),
            "accuracy": {f: hits[f] / 100 for f in fields}, "unsafe_routes": unsafe,
            "latency_ms": {"p50": statistics.median(latency), "p95": sorted(latency)[94]},
            "api_cost": 0, "currency": "USD", "scope": "synthetic_only",
            "status": "PASS" if all(r["pass"] for r in records) and not unsafe else "FAIL",
            "limitations": ["10 symptom templates across 10 brands; exploratory baseline only",
                            "No LLM accuracy, token cost, production suitability or provider comparison proven"],
            "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--corpus")
    args = parser.parse_args()
    report = benchmark()
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    if args.corpus:
        Path(args.corpus).write_text(json.dumps(corpus(), ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({k: v for k, v in report.items() if k != "records"}, ensure_ascii=False))
    raise SystemExit(0 if report["status"] == "PASS" else 1)
