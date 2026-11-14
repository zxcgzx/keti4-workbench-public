"""Deterministic, inspectable AC-OTS template. No solver is invoked.
B9 is an external scenario contract, not an expanded AC N-1 certificate.
Angles VA are radians; SHIFT follows MATPOWER branch phase-shift degrees.
"""
import math
import pyomo.environ as pyo

DEFAULT_BETA = __DEFAULT_BETA__

def build_model(data, spec):
    m = pyo.ConcreteModel(name="keti4_specification")
    bus, gen, branch = data["bus"], data["gen"], data["branch"]
    base = float(data.get("meta", {}).get("baseMVA", 100.0))
    count = int(spec.get("omega", {}).get("periods", 16))
    times = list(range(1, count + 1))
    def period_frame(t):
        frames = data.get("period_data")
        if frames is None:
            return data
        frame = frames.get(t, frames.get(str(t)))
        if frame is None:
            raise ValueError("Missing period_data frame: " + str(t))
        return frame
    frames = {t: period_frame(t) for t in times}
    for t, frame in frames.items():
        for component in ("bus", "gen", "branch"):
            if set(frame[component]) != set(data[component]):
                raise ValueError("Object identities changed in period " + str(t))
        if float(frame.get("meta", {}).get("baseMVA", base)) != base:
            raise ValueError("baseMVA changed in period " + str(t))
        if any(int(frame["gen"][g]["GEN_BUS"]) != int(gen[g]["GEN_BUS"]) for g in gen):
            raise ValueError("Generator bus mapping changed in period " + str(t))
        if any((int(frame["branch"][l]["F_BUS"]), int(frame["branch"][l]["T_BUS"])) != (int(branch[l]["F_BUS"]), int(branch[l]["T_BUS"])) for l in branch):
            raise ValueError("Branch endpoint mapping changed in period " + str(t))
    changes = []
__DELTA_BLOCKS__
    extra_ops = []
__STRUCTURAL_BLOCKS__
    def value(component, target, param, t):
        original = float(frames[t][component][target][param])
        for item in changes:
            if item["component"] == component and item["target"] == target and item["param"] == param and (not item.get("periods") or t in item["periods"]):
                v = item.get("value")
                if v is None:
                    raise ValueError("Unconfirmed modification: " + item["id"])
                op = item.get("operation")
                if op == "scale": original *= float(v)
                elif op == "set": original = float(v)
                elif op == "add": original += float(v)
                else: raise ValueError("Unsupported operation: " + str(op))
        return original
    rule = spec.get("omega", {}).get("switchable_rule", "meshed_in_service")
    forced = {item["target"] for item in changes if item["component"] == "branch" and item["param"] == "BR_STATUS"}
    # Canonical model choice: stale structural operators cannot override omega.
    fixed = spec.get("omega", {}).get("problem") == "multi_period_acopf"
    pairs = []
    for t in times:
        frame = frames[t]
        candidates = [lid for lid, row in frame["branch"].items() if int(row["BR_STATUS"]) == 1 and lid not in forced]
        if rule != "all_in_service":
            candidates = [lid for lid in candidates if lid in frame.get("meshed", data.get("meshed", []))]
        if rule == "meshed_kv_ge_110":
            candidates = [lid for lid in candidates if max(float(frame["bus"]["B"+str(int(branch[lid]["F_BUS"]))]["BASE_KV"]), float(frame["bus"]["B"+str(int(branch[lid]["T_BUS"]))]["BASE_KV"])) >= 110]
        if not fixed:
            pairs.extend((lid, t) for lid in candidates)
    free_lines = {lid for lid, t in pairs}
    eligible = [lid for lid in branch if lid in free_lines]
    m.N = pyo.Set(initialize=list(bus), ordered=True)
    m.G = pyo.Set(initialize=list(gen), ordered=True)
    m.L = pyo.Set(initialize=list(branch), ordered=True)
    m.T = pyo.Set(initialize=times, ordered=True)
    m.S = pyo.Set(initialize=eligible, ordered=True)
    m.ST = pyo.Set(dimen=2, initialize=pairs, ordered=True)
    m.V = pyo.Var(m.N, m.T, initialize=lambda _, i, t: float(frames[t]["bus"][i]["VM"]))
    m.theta = pyo.Var(m.N, m.T, initialize=lambda _, i, t: float(frames[t]["bus"][i]["VA"]))
    m.Pg = pyo.Var(m.G, m.T, initialize=lambda _, g, t: float(frames[t]["gen"][g]["PG"]))
    m.Qg = pyo.Var(m.G, m.T, initialize=lambda _, g, t: float(frames[t]["gen"][g]["QG"]))
    m.Pf = pyo.Var(m.L, m.T, initialize=lambda _, l, t: float(frames[t]["branch"][l]["PF"]))
    m.Qf = pyo.Var(m.L, m.T, initialize=lambda _, l, t: float(frames[t]["branch"][l]["QF"]))
    m.Pr = pyo.Var(m.L, m.T, initialize=lambda _, l, t: float(frames[t]["branch"][l]["PT"]))
    m.Qr = pyo.Var(m.L, m.T, initialize=lambda _, l, t: float(frames[t]["branch"][l]["QT"]))
    m.z = pyo.Var(m.ST, domain=pyo.Binary, initialize=1)
    # >>> [B7] Non-switchable and maintenance branches use fixed status expressions.
    def status(_, l, t):
        return m.z[l,t] if (l,t) in m.ST else value("branch", l, "BR_STATUS", t)
    m.status = pyo.Expression(m.L, m.T, rule=status)
    # <<< [B7]
    outgoing = {i: [] for i in bus}
    incoming = {i: [] for i in bus}
    generators = {i: [] for i in bus}
    for l, row in branch.items():
        outgoing["B"+str(int(row["F_BUS"]))].append(l)
        incoming["B"+str(int(row["T_BUS"]))].append(l)
    for g, row in gen.items(): generators["B"+str(int(row["GEN_BUS"]))].append(g)
    # >>> [B1] Active-power balance in MW (shunts also use named units).
    m.active_balance = pyo.Constraint(m.N, m.T, rule=lambda _, i, t:
        sum(m.Pg[g,t] for g in generators[i]) - value("bus",i,"PD",t) - value("bus",i,"GS",t)*m.V[i,t]**2 ==
        sum(m.Pf[l,t] for l in outgoing[i]) + sum(m.Pr[l,t] for l in incoming[i]))
    # <<< [B1]
    # >>> [B2] Reactive-power balance in Mvar.
    m.reactive_balance = pyo.Constraint(m.N, m.T, rule=lambda _, i, t:
        sum(m.Qg[g,t] for g in generators[i]) - value("bus",i,"QD",t) + value("bus",i,"BS",t)*m.V[i,t]**2 ==
        sum(m.Qf[l,t] for l in outgoing[i]) + sum(m.Qr[l,t] for l in incoming[i]))
    # <<< [B2]
    # >>> [B3] MATPOWER pi-branch equations with TAP and SHIFT; conservative finite M.
    m.branch_coupling = pyo.ConstraintList()
    for l, reference_row in branch.items():
        f, r = "B"+str(int(reference_row["F_BUS"])), "B"+str(int(reference_row["T_BUS"]))
        for t in times:
            row = frames[t]["branch"][l]
            resistance, reactance = float(row["BR_R"]), float(row["BR_X"])
            if resistance*resistance + reactance*reactance <= 0:
                raise ValueError("Zero impedance branch: " + l)
            g = resistance/(resistance*resistance + reactance*reactance)
            b = -reactance/(resistance*resistance + reactance*reactance)
            tau = float(row["TAP"]) or 1.0
            phase = math.radians(float(row["SHIFT"]))
            bc = float(row["BR_B"])
            gff, bff = g/tau**2, (b+bc/2)/tau**2
            gtt, btt = g, b+bc/2
            gft = -(g*math.cos(phase)-b*math.sin(phase))/tau
            bft = -(g*math.sin(phase)+b*math.cos(phase))/tau
            gtf = -(g*math.cos(phase)+b*math.sin(phase))/tau
            btf = -(-g*math.sin(phase)+b*math.cos(phase))/tau
            big_m = max(10000.0, 4*base*1.2**2*(abs(gff)+abs(bff)+abs(gtt)+abs(btt)+abs(gft)+abs(bft)+abs(gtf)+abs(btf)))
            vf, vr, angle = m.V[f,t], m.V[r,t], m.theta[f,t]-m.theta[r,t]
            calculated = (
                base*(gff*vf**2+vf*vr*(gft*pyo.cos(angle)+bft*pyo.sin(angle))),
                base*(-bff*vf**2+vf*vr*(gft*pyo.sin(angle)-bft*pyo.cos(angle))),
                base*(gtt*vr**2+vf*vr*(gtf*pyo.cos(angle)-btf*pyo.sin(angle))),
                base*(-btt*vr**2+vf*vr*(-gtf*pyo.sin(angle)-btf*pyo.cos(angle))),
            )
            for flow, expr in zip((m.Pf[l,t],m.Qf[l,t],m.Pr[l,t],m.Qr[l,t]), calculated):
                m.branch_coupling.add(flow-expr <= big_m*(1-m.status[l,t]))
                m.branch_coupling.add(expr-flow <= big_m*(1-m.status[l,t]))
                m.branch_coupling.add(flow <= big_m*m.status[l,t])
                m.branch_coupling.add(-flow <= big_m*m.status[l,t])
    # <<< [B3]
    # >>> [B4] Both-end apparent-power ratings; zero RATE_A means no thermal bound.
    m.thermal = pyo.ConstraintList()
    for l in branch:
        for t in times:
            rate = value("branch",l,"RATE_A",t)
            if rate > 0:
                m.thermal.add(m.Pf[l,t]**2+m.Qf[l,t]**2 <= m.status[l,t]*rate**2)
                m.thermal.add(m.Pr[l,t]**2+m.Qr[l,t]**2 <= m.status[l,t]*rate**2)
    # <<< [B4]
    # >>> [B5] Voltage bounds.
    m.voltage_lower = pyo.Constraint(m.N,m.T,rule=lambda _,i,t: m.V[i,t] >= value("bus",i,"VMIN",t))
    m.voltage_upper = pyo.Constraint(m.N,m.T,rule=lambda _,i,t: m.V[i,t] <= value("bus",i,"VMAX",t))
    # <<< [B5]
    # >>> [B6] Generator status and P/Q bounds.
    m.pg_lower = pyo.Constraint(m.G,m.T,rule=lambda _,g,t: m.Pg[g,t] >= value("gen",g,"GEN_STATUS",t)*value("gen",g,"PMIN",t))
    m.pg_upper = pyo.Constraint(m.G,m.T,rule=lambda _,g,t: m.Pg[g,t] <= value("gen",g,"GEN_STATUS",t)*value("gen",g,"PMAX",t))
    m.qg_lower = pyo.Constraint(m.G,m.T,rule=lambda _,g,t: m.Qg[g,t] >= value("gen",g,"GEN_STATUS",t)*value("gen",g,"QMIN",t))
    m.qg_upper = pyo.Constraint(m.G,m.T,rule=lambda _,g,t: m.Qg[g,t] <= value("gen",g,"GEN_STATUS",t)*value("gen",g,"QMAX",t))
    # <<< [B6]
    # >>> [B8] Reference-bus angle.
    reference = next((i for i,r in bus.items() if int(r["BUS_TYPE"])==3), next(iter(bus)))
    m.reference_angle = pyo.Constraint(m.T,rule=lambda _,t: m.theta[reference,t] == 0)
    # <<< [B8]
    # >>> [B0] Placeholder objective: deviation from observed dispatch, no cost data.
    objective = sum((m.Pg[g,t]-float(gen[g]["PG"]))**2 for g in m.G for t in m.T)
    # <<< [B0]
    switching = any(x["op_id"] in ("switching_limit_per_period","switching_limit_total","switching_penalty") for x in extra_ops)
    if switching:
        m.change = pyo.Var(m.S,m.T,domain=pyo.NonNegativeReals,initialize=0)
        m.change_positive = pyo.Constraint(m.S,m.T,rule=lambda _,l,t:
            m.change[l,t] >= m.status[l,t]-(m.status[l,t-1] if t>1 else float(branch[l]["BR_STATUS"])))
        m.change_negative = pyo.Constraint(m.S,m.T,rule=lambda _,l,t:
            m.change[l,t] >= (m.status[l,t-1] if t>1 else float(branch[l]["BR_STATUS"]))-m.status[l,t])
    m.additional = pyo.ConstraintList()
    for op in extra_ops:
        name, params = op["op_id"], op.get("params",{})
        beta = float(params["beta"]) if params.get("beta") is not None else DEFAULT_BETA
        if name == "switching_limit_per_period":
            for t in times:
                m.additional.add(sum(m.change[l,t] for l in m.S) <= float(params["k"]) if len(m.S) else pyo.Constraint.Feasible)
        elif name == "switching_limit_total":
            m.additional.add(sum(m.change[l,t] for l in m.S for t in times) <= float(params.get("k",params.get("K"))) if len(m.S) else pyo.Constraint.Feasible)
        elif name == "section_limit":
            for t in times:
                flow = sum(m.Pf[l,t] for l in params["branches"])
                if params.get("direction","forward") in ("forward","both"): m.additional.add(flow <= float(params["limit_mw"]))
                if params.get("direction") in ("reverse","both"): m.additional.add(-flow <= float(params["limit_mw"]))
        elif name == "switching_penalty":
            objective += beta*sum(m.change[l,t] for l in m.S for t in times)
        elif name == "loss_min":
            objective += beta*sum(m.Pf[l,t]+m.Pr[l,t] for l in m.L for t in times)
        elif name == "voltage_deviation":
            objective += beta*sum((m.V[i,t]-1)**2 for i in m.N for t in times)
    m.objective = pyo.Objective(expr=objective)
    # >>> [B9] N-1 contract. Expansion is delegated to the external solver chain.
    m.n1_requires_external_expansion = bool(spec.get("omega",{}).get("n_minus_1",True))
    # This module does not certify AC N-1 feasibility. Trial build sets n_minus_1=False.
    # <<< [B9]
__CUSTOM_BLOCKS__
    return m