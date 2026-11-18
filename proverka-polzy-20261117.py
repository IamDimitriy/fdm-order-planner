"""Сравнение с простым правилом на сохранённых учебных примерах. Без записи файлов."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
parameters = json.loads((root / "parameters.json").read_text(encoding="utf-8"))
results = json.loads((root / "results.json").read_text(encoding="utf-8"))


def next_operator_slot(earliest, duration, windows, occupied):
    for left, right in windows:
        start = max(earliest, left)
        for busy_start, busy_end in sorted(occupied):
            if busy_end <= start:
                continue
            if busy_start >= start + duration:
                break
            start = busy_end
        if start + duration <= right:
            return start
    return None


for fixture, saved in zip(parameters["cases"], results["cases"]):
    assert fixture["id"] == saved["id"]
    new_jobs = [job for job in fixture["jobs"] if job["kind"] == "new"]
    assert len(new_jobs) == 1
    job = new_jobs[0]
    old = saved["old_plan"]["schedule"]
    occupied = [row[op] for row in old.values() for op in ("setup", "unload")]
    candidates = []
    for printer, print_minutes in job["print_minutes"].items():
        release = max([parameters["printers"][printer]["available_from"]]
                      + [row["unload"][1] for row in old.values()
                         if row["printer"] == printer])
        setup = next_operator_slot(release, job["setup"],
                                   parameters["operator_windows"], occupied)
        if setup is None:
            continue
        print_end = setup + job["setup"] + print_minutes
        unload = next_operator_slot(print_end, job["unload"],
                                    parameters["operator_windows"], occupied)
        if unload is not None:
            candidates.append((unload + job["unload"], printer))
    simple, printer = min(candidates)
    print(json.dumps({"case": fixture["id"], "simple_ready": simple,
                      "printer": printer,
                      "saved_exhaustive_ready": saved["protected_best"]["new_ready"],
                      "all_options": candidates}, ensure_ascii=False))
