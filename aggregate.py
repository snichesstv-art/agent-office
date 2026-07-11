# -*- coding: utf-8 -*-
"""Agent Office — Claude Code 세션 통합 집계기 (배포판)
~/.claude/projects/*/*.jsonl 을 스캔해 세션=에이전트로 집계.
개인 확장(할일/마감/정산 등)은 config.json 으로 연결 (없으면 해당 패널만 비어 보임)."""
import os, json, glob, re, datetime as dt

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".claude", "projects")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIVE_HOURS = 12      # 최근 N시간 내 활동 = ACTIVE
STALL_DAYS = 4         # 아이디어 프로젝트 중단 판정 일수

# ---- 사용자 설정 (config.json, 전부 선택사항) ----
def _load_config():
    p = os.path.join(OUT_DIR, "config.json")
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}
CFG = _load_config()
IDEA_PREFIX = CFG.get("idea_prefix", "Money")   # 이 접두사의 프로젝트 = 아이디어 파이프라인
USD_KRW = CFG.get("usd_krw", 1400)

# API 환산 단가 (USD per 1M tokens): (input, output). cache_read=input*0.1, cache_write=input*1.25
PRICING = {"opus": (5.0, 25.0), "fable": (10.0, 50.0), "sonnet": (3.0, 15.0), "haiku": (1.0, 5.0)}

def calc_cost(model, inp, out, cread, ccreate):
    m = (model or "").lower()
    for k, (pi, po) in PRICING.items():
        if k in m:
            return (inp*pi + out*po + cread*pi*0.1 + ccreate*pi*1.25) / 1e6
    return (inp*5.0 + out*25.0 + cread*0.5 + ccreate*6.25) / 1e6

def parse_ts(s):
    if not s: return None
    try: return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception: return None

def readable_project(folder):
    name = folder.split("Desktop-")[-1] if "Desktop-" in folder else folder
    return name.strip("-") or folder

def first_topic(path):
    summary = first_user = None
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i > 60 and first_user: break
                try: o = json.loads(line)
                except Exception: continue
                if o.get("type") == "summary" and o.get("summary"):
                    summary = o["summary"]; break
                if o.get("type") == "user" and first_user is None:
                    msg = o.get("message", {}) or {}
                    c = msg.get("content"); txt = ""
                    if isinstance(c, str): txt = c
                    elif isinstance(c, list):
                        for p in c:
                            if isinstance(p, dict) and p.get("type") == "text":
                                txt = p.get("text", ""); break
                    txt = txt.strip()
                    if txt and not txt.startswith("<") and "command-name" not in txt:
                        first_user = txt
    except Exception: pass
    return re.sub(r"\s+", " ", (summary or first_user or "(제목 없음)")).strip()[:70]

def money_pipeline(agents):
    """아이디어 프로젝트(IDEA_PREFIX*) 중단 감지."""
    byp = {}
    for a in agents:
        if not a["project"].lower().startswith(IDEA_PREFIX.lower()): continue
        p = byp.setdefault(a["project"], dict(project=a["project"], sessions=0, out=0, mins=None, topic=""))
        p["sessions"] += 1; p["out"] += a["out"]
        if a["mins_since"] is not None and (p["mins"] is None or a["mins_since"] < p["mins"]):
            p["mins"] = a["mins_since"]; p["topic"] = a["topic"]
    out = []
    for p in byp.values():
        days = (p["mins"] // 1440) if p["mins"] is not None else None
        out.append(dict(project=p["project"], sessions=p["sessions"], out=p["out"], days=days,
                        topic=p["topic"][:90], stalled=(days is None or days >= STALL_DAYS)))
    out.sort(key=lambda x: (x["days"] if x["days"] is not None else 9999))
    return out

def office_memory():
    """모든 프로젝트의 memory/*.md → 지식 그래프 노드."""
    out = []
    for f in sorted(glob.glob(os.path.join(PROJ, "*", "memory", "*.md"))):
        base = os.path.splitext(os.path.basename(f))[0]
        if base == "MEMORY": continue
        try: txt = open(f, encoding="utf-8").read()
        except Exception: continue
        links = list(set(re.findall(r"\[\[([^\]]+)\]\]", txt)))
        dm = re.search(r"description:\s*[\"']?(.+?)[\"']?\s*$", txt, re.M)
        out.append(dict(name=base, desc=(dm.group(1)[:80] if dm else ""), links=links))
    return out

def office_recur():
    """반복업무 D-day — config.json 의 recur: [["제목", 기준일|"EOM", 주기개월], ...]"""
    rules = CFG.get("recur", [])
    import calendar
    today = dt.date.today(); out = []
    for item in rules:
        try: title, day, step = item[0], item[1], (item[2] if len(item) > 2 else 1)
        except Exception: continue
        y, m = today.year, today.month
        for _ in range(6):
            d = calendar.monthrange(y, m)[1] if day == "EOM" else min(int(day), calendar.monthrange(y, m)[1])
            due = dt.date(y, m, d)
            if due >= today: break
            m += step; y += (m - 1) // 12; m = (m - 1) % 12 + 1
        out.append(dict(title=title, due=due.isoformat(), dday=(due - today).days))
    out.sort(key=lambda r: r["dday"])
    return out

def build(write=True):
    NOW = dt.datetime.now(dt.timezone.utc)
    agents = []; by_hour = {}; model_tok = {}
    grand = dict(inp=0, out=0, cread=0, ccreate=0)
    for proj_dir in sorted(glob.glob(os.path.join(PROJ, "*"))):
        if not os.path.isdir(proj_dir): continue
        proj = readable_project(os.path.basename(proj_dir))
        for path in glob.glob(os.path.join(proj_dir, "*.jsonl")):
            sid = os.path.splitext(os.path.basename(path))[0]
            inp=out=cread=ccreate=0; msgs=0; first=last=None; model=None
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        try: o = json.loads(line)
                        except Exception: continue
                        ts = parse_ts(o.get("timestamp"))
                        if ts:
                            if first is None or ts < first: first = ts
                            if last is None or ts > last: last = ts
                        msg = o.get("message") or {}; u = msg.get("usage")
                        if u:
                            msgs += 1
                            i_=u.get("input_tokens",0) or 0; o_=u.get("output_tokens",0) or 0
                            cr=u.get("cache_read_input_tokens",0) or 0; cc=u.get("cache_creation_input_tokens",0) or 0
                            inp+=i_; out+=o_; cread+=cr; ccreate+=cc
                            if msg.get("model"): model = msg.get("model")
                            if ts:
                                hk = ts.strftime("%Y-%m-%d %H")
                                by_hour[hk] = by_hour.get(hk,0) + i_ + o_ + cc
            except Exception: continue
            if msgs == 0: continue
            total = inp+out+cread+ccreate
            dur_h = ((last-first).total_seconds()/3600.0) if (first and last) else 0
            active = bool(last and (NOW-last).total_seconds() <= ACTIVE_HOURS*3600)
            tph = round((inp+out+ccreate)/dur_h) if dur_h>0.05 else (inp+out+ccreate)
            mins_since = int((NOW-last).total_seconds()/60) if last else None
            cost = round(calc_cost(model, inp, out, cread, ccreate), 2)
            agents.append(dict(
                id=sid[:8], sid=sid, project=proj, topic=first_topic(path), model=model or "?",
                cost=cost, messages=msgs, total=total, out=out, inp=inp, cache=cread+ccreate,
                first=first.isoformat() if first else None,
                last=last.isoformat() if last else None,
                dur_h=round(dur_h,2), tph=tph, active=active, mins_since=mins_since))
            grand["inp"]+=inp; grand["out"]+=out; grand["cread"]+=cread; grand["ccreate"]+=ccreate
            if model: model_tok[model]=model_tok.get(model,0)+total
    agents.sort(key=lambda a:(a["last"] or ""), reverse=True)
    end = NOW.replace(minute=0, second=0, microsecond=0); hours=[]
    for k in range(47,-1,-1):
        h = end - dt.timedelta(hours=k)
        hours.append(dict(t=h.strftime("%m/%d %H시"), tok=by_hour.get(h.strftime("%Y-%m-%d %H"),0)))
    firsts = [dt.datetime.fromisoformat(a["first"]) for a in agents if a["first"]]
    data = dict(
        generated=NOW.isoformat(), active_hours=ACTIVE_HOURS,
        totals=dict(agents=len(agents), active=sum(1 for a in agents if a["active"]),
            projects=len(set(a["project"] for a in agents)),
            tok_total=sum(a["total"] for a in agents),
            tok_out=grand["out"], tok_in=grand["inp"], tok_cache=grand["cread"]+grand["ccreate"],
            cost_total=round(sum(a["cost"] for a in agents), 2),
            days_span=max(1, (NOW - min(firsts)).days) if firsts else 1,
            usd_krw=USD_KRW),
        by_model=[dict(model=m, tok=t) for m,t in sorted(model_tok.items(), key=lambda x:-x[1])],
        by_hour=hours, agents=agents,
        office=dict(tasks=[], gongmun={}, pending=[], money=money_pipeline(agents),
                    recur=office_recur(), memory=office_memory(), outputs=[], vendors=[], settle=[]),
    )
    if write:
        with open(os.path.join(OUT_DIR,"dashboard_data.json"),"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=1)
    return data

if __name__ == "__main__":
    d = build(); t = d["totals"]
    print(f"에이전트 {t['agents']} · 활성 {t['active']} · 누적비용(환산) ${t['cost_total']}")
