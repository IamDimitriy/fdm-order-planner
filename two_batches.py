"""Срок готовности комплекта при последовательном и совместном выборе принтеров."""
from calculate import enumerate_plans, choose, validate

shop = {"printers": {p: {"available_from": 0} for p in ("P1", "P2")},
        "operator_windows": [[0, 180]], "horizon": 180}
old = dict(id="old", kind="accepted", printer="P2", rank=0, setup=5, unload=5, print_minutes={"P2":5})
flex = dict(id="flex", kind="new", rank=1, setup=5, unload=5, print_minutes={"P1":40, "P2":45})
special = dict(id="special", kind="new", rank=2, setup=5, unload=5, print_minutes={"P1":60})

def best(jobs, deadlines=None, fixed=None):
    plans, _ = enumerate_plans(shop, jobs, deadlines=deadlines, fixed=fixed)
    plan = choose(jobs, plans)
    assert plan and not validate(shop, jobs, plan, deadlines)
    return plan

deadlines = {"old": best([old])["schedule"]["old"]["unload"][1]}
first = best([old, flex], deadlines)
jobs = [old, flex, special]
sequential = best(jobs, deadlines, {"flex": first["schedule"]["flex"]["printer"]})
joint = best(jobs, deadlines)
assert deadlines["old"] == 15 and (sequential["new_ready"], joint["new_ready"]) == (125, 75)
for plan in (sequential, joint):
    print(plan["new_ready"], plan["schedule"])
