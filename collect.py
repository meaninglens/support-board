"""지원사업 공고 수집기.

K-Startup(진행 중 공고와 상세), 기업마당(진행 중 공고와 상세), 위비티(취업/창업 공모전과 상세),
충북대 창업지원단(프로그램 목록)을 공개 웹 페이지에서 읽어 사이트용 데이터를 만든다.
API 키는 쓰지 않는다. 상세 페이지는 처음 본 공고만 받고 나머지는 `data/state.json`에 쌓아 둔다.

결과:
- site/data/items.json  사이트가 읽는 공고 목록(상세 내용, 처음 본 날짜, 조건 표시 포함)
- data/state.json       공고별 처음 본 날짜와 상세 내용 캐시

사이트의 "지금 새로 모으기" 버튼이 GitHub Actions(.github/workflows/collect.yml)로 이 파일을 돌린다.
직접 돌릴 때: python3 collect.py   (표준 라이브러리만. 첫 실행 15~20분, 그다음은 3~5분)
"""
import datetime as dt
import html as H
import json
import pathlib
import re
import time
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).parent
STATE = ROOT / "data" / "state.json"
ITEMS = ROOT / "site" / "data" / "items.json"
UA = {"User-Agent": "Mozilla/5.0 (meaningworld support-board; contact meaninglens0@gmail.com)"}
KST = ZoneInfo("Asia/Seoul")
TODAY = dt.datetime.now(KST).date().isoformat()

KS = "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do"
BZ = "https://www.bizinfo.go.kr/web/lay1/bbs/S1T122C128/AS/74/list.do"
BZD = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId="
WV = "https://www.wevity.com/index.php"
CBNU = "https://startup.cbnu.ac.kr"

REGIONS = "서울|경기|부산|대구|인천|광주|대전|울산|세종|강원|충남|충북|전북|전남|경북|경남|제주"
KEYWORDS = ["심리", "정신건강", "마음", "멘탈", "헬스케어", "디지털헬스", "웰빙", "AI", "인공지능",
            "소셜벤처", "사회적", "임팩트", "콘텐츠", "청년", "대학원", "대학생", "아이디어", "공모전",
            "경진대회", "해커톤", "충북", "청주", "예비창업"]
NOISE = ("신청 시 요청하는 정보", "제출하신 서류는", "접수 바로가기", "자세한 내용은 첨부파일")


def get(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")


def text(s):
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def lines(h):
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", h, flags=re.S)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", t)
    t = H.unescape(re.sub(r"<[^>]+>", "\n", t)).replace("\xa0", " ")
    t = re.sub(r"[ \t\r]+", " ", t)
    return [l.strip() for l in t.split("\n") if l.strip()]


def clean(ls):
    return "\n".join(l for l in ls if not any(n in l for n in NOISE)).strip()


# ---------- K-Startup ----------
def ks_list():
    items = {}
    for page in range(1, 60):
        body = get(f"{KS}?page={page}")
        body = body[body.find('id="bizPbancList"'):]
        new = 0
        for li in re.split(r'<li class="notice">', body)[1:]:
            m = re.search(r"go_view\((\d+)\)", li)
            if not m:
                continue
            sn = m.group(1)
            spans = [text(x) for x in re.findall(r'<span class="list">(.*?)</span>', li, re.S)]
            meta = dict(re.match(r"(등록일자|시작일자|마감일자)\s*(.*)", x).groups()
                        for x in spans if re.match(r"(등록일자|시작일자|마감일자)", x))
            rest = [x for x in spans if not re.match(r"(등록일자|시작일자|마감일자|조회)", x)]
            new += sn not in items
            items[sn] = dict(id=sn, title=text(re.search(r'<p class="tit">(.*?)</p>', li, re.S).group(1)),
                             org=rest[1] if len(rest) > 1 else "", reg=meta.get("등록일자"),
                             start=meta.get("시작일자"), end=meta.get("마감일자"),
                             url=f"{KS}?schM=view&pbancSn={sn}")
        if new == 0:
            break
        time.sleep(0.4)
    return list(items.values())


KS_FIELDS = ["지원분야", "대상연령", "기관구분", "담당부서", "지역", "접수기간", "주관기관명", "대상", "창업업력", "연락처"]
KS_SECTIONS = ["신청기간", "신청방법", "신청대상", "제외대상", "제출서류", "선정절차 및 평가방법", "지원내용", "문의처"]


def ks_detail(url):
    h = get(url)
    apply = re.search(r"fn_open_window\('([^']+)'\);\" class=\"btn_check\" style", h)
    ls = lines(h)
    ls = ls[ls.index("사업안내 바로가기") + 1:] if "사업안내 바로가기" in ls else ls
    d = {}
    i = 0
    while i < len(ls) - 1 and len(d) < len(KS_FIELDS):
        if ls[i] in KS_FIELDS and ls[i] not in d:
            d[ls[i]] = ls[i + 1]
            last = i + 1
            i += 2
        else:
            i += 1
    body = ls[last + 1:] if d else ls
    end = next((k for k, l in enumerate(body) if l.startswith("K-Startup에 공고되는") or l == "첨부파일 일괄 다운로드"), len(body))
    body = body[:end]
    sec = []
    start = body.index("신청방법 및 대상") if "신청방법 및 대상" in body else len(body)
    sec.append(["개요", clean(body[1:start])])  # body[0]은 제목 반복
    cur, buf = None, []
    for l in body[start + 1:]:
        if l in KS_SECTIONS and (cur is None or l != cur):
            if cur:
                sec.append([cur, clean(buf)])
            cur, buf = l, []
        elif l in ("신청 절차",):
            continue
        else:
            buf.append(l)
    if cur:
        sec.append([cur, clean(buf)])
    for s_ in sec:
        ls_ = s_[1].split("\n")
        if ls_ and ls_[0] == s_[0]:
            ls_ = ls_[1:]
        s_[1] = (" ".join(ls_) if s_[0] == "신청기간" else "\n".join(ls_)).strip()
    d["sections"] = [s_ for s_ in sec if s_[1]]
    if apply:
        a = apply.group(1)
        d["apply"] = a if a.startswith("http") else "https://" + a
    return d


# ---------- 기업마당 ----------
def bz_list():
    items = {}
    for page in range(1, 150):
        h = get(f"{BZ}?rows=15&cpage={page}&schEndAt=N")
        rows = re.findall(r"<tr>(.*?)</tr>", h[h.find("<tbody"):h.find("</tbody>")], re.S)
        new = 0
        for r in rows:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
            m = re.search(r"pblancId=(PBLN_\d+)", r)
            if len(tds) < 7 or not m:
                continue
            new += m.group(1) not in items
            items[m.group(1)] = dict(id=m.group(1), cat=text(tds[1]), title=text(tds[2]), period=text(tds[3]),
                                     ministry=text(tds[4]), org=text(tds[5]), reg=text(tds[6]),
                                     url=BZD + m.group(1))
        if new == 0 or len(rows) < 15:
            break
        time.sleep(0.3)
    return list(items.values())


def bz_detail(url):
    h = get(url)
    v0 = h.find('class="view_cont"')
    box = h[v0:h.find('class="tag_list"', v0)] if v0 > 0 else ""
    sec = []
    for t, v in re.findall(r'<span class="s_title">(.*?)</span>\s*<div class="txt"[^>]*>(.*?)</div>\s*</li>', box, re.S):
        t = text(t)
        if t == "사업신청 사이트":
            a = re.search(r'href="([^"]+)"', v)
            if a:
                sec.append([t, a.group(1)])
            continue
        v = (" " if t == "신청기간" else "\n").join(lines(v))
        if v:
            sec.append([t, v])
    tags = [text(x) for x in re.findall(r'<span style="color:#4098a8" tabindex="0">(.*?)</span>', h)]
    return {"sections": sec, "tags": sorted(set(t.lstrip("#") for t in tags))}


# ---------- 위비티 ----------
def wv_list():
    out = []
    for page in (1, 2, 3):
        h = get(f"{WV}?c=find&s=1&gub=1&cidx=88&gp={page}")
        for li in re.findall(r'<li\s*(?:class=.bg.)?\s*><!--class="bg" -->(.*?)</li>', h, re.S):
            a = re.search(r'href="[^"]*ix=(\d+)"[^>]*>(.*?)</a>', li, re.S)
            day = re.search(r'<div class="day">(.*?)</div>', li, re.S)
            org = re.search(r'<div class="organ">(.*?)</div>', li, re.S)
            out.append(dict(id=a.group(1), title=text(re.sub(r"<span.*?</span>", "", a.group(2))),
                            org=text(org.group(1)) if org else "", day=text(day.group(1)) if day else "",
                            url=f"{WV}?c=find&s=1&gub=1&cidx=88&gbn=view&ix={a.group(1)}"))
    return out


WV_FIELDS = ["분야", "응모대상", "주최/주관", "후원/협찬", "접수기간", "참가조건", "총 상금", "1등 상금", "홈페이지"]


def wv_detail(url):
    ls = lines(get(url))
    i = next((k for k, l in enumerate(ls) if l.startswith("조회수")), 0)
    ls = ls[i + 1:]
    d, sec = {}, []
    for k, l in enumerate(ls[:40]):
        if l in WV_FIELDS and l not in d and k + 1 < len(ls) and ls[k + 1] not in WV_FIELDS:
            d[l] = ls[k + 1]
    body = ""
    if "상세내용" in ls:  # 위비티가 쓴 본문은 저장하지 않고, 판단(관련 낱말·사업자 필요 여부)에만 쓴다
        s = ls.index("상세내용") + 1
        e = next((k for k in range(s, len(ls)) if ls[k].startswith("본 정보의 사실 여부")), len(ls))
        body = " ".join(ls[s:e])
    sec = [[f, d[f]] for f in ("응모대상", "주최/주관", "접수기간", "총 상금", "1등 상금") if f in d]
    return {"fields": d, "sections": sec, "kw": kw_of(body), "stage": stage_from(d.get("응모대상", "") + " " + body)}


# ---------- 충북대 ----------
def cbnu_list():
    h = get(CBNU)
    out = []
    for u, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', h, re.S):
        t = text(t)
        if "기간:" in t:
            name, period = t.split("기간:", 1)
            ds = re.findall(r"\d{4}-\d{2}-\d{2}", period)
            out.append(dict(id=re.sub(r"\W", "", name)[:40], title=name.strip(), period=period.strip(),
                            start=ds[0] if ds else None, end=ds[-1] if ds else None, url=H.unescape(u)))
    return out


# ---------- 정리 ----------
def ymd(s):
    m = re.search(r"(\d{4})[.-](\d{2})[.-](\d{2})", s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def stage_from(text_):
    if re.search(r"예비\s?창업|예비\s?\(?창업|대학\(?원?\)?생|개인 또는 팀|누구나", text_):
        return "예비 가능"
    if re.search(r"기업|사업자|소상공인|법인", text_):
        return "사업자 대상"
    return "확인 필요"


def kw_of(s):
    return [k for k in KEYWORDS if k in s]


def build(raw, state):
    seen, det = state.setdefault("first_seen", {}), state.setdefault("details", {})
    items = []

    for x in raw["kstartup"]:
        key = "ks:" + x["id"]
        d = det.get(key, {})
        period = d.get("접수기간", "")
        tm = re.search(r"(\d{1,2}:\d{2})\s*$", period)
        age = d.get("창업업력")
        stage = ("예비 가능" if re.search("예비|전체", age) else "사업자 대상") if age else stage_from(json.dumps(d, ensure_ascii=False))
        full = x["title"] + json.dumps(d.get("sections", []), ensure_ascii=False)
        items.append(dict(id=key, src="K-Startup", title=x["title"], org=d.get("주관기관명") or x["org"],
                          url=x["url"], apply=d.get("apply"), reg=x.get("reg"), start=x.get("start"), end=x.get("end"),
                          endTime=tm.group(1) if tm else "", region=d.get("지역", ""), stage=stage,
                          cat=d.get("지원분야", ""), target=d.get("대상", ""), age=d.get("대상연령", ""),
                          career=age or "", kw=kw_of(full), sections=d.get("sections", [])))

    for x in raw["bizinfo"]:
        key = "bz:" + x["id"]
        d = det.get(key, {})
        reg = re.match(rf"^\[({REGIONS})", x["title"])
        tag_regions = [t for t in d.get("tags", []) if re.fullmatch(rf"({REGIONS})(광주)?", t)]
        overview = " ".join(v for k, v in d.get("sections", []) if k == "사업개요")
        items.append(dict(id=key, src="기업마당", title=x["title"], org=x["org"], ministry=x["ministry"], url=x["url"],
                          reg=ymd(x.get("reg")), start=ymd(x["period"].split("~")[0]) if "~" in x["period"] else None,
                          end=ymd(x["period"].split("~")[-1]), endLabel="" if ymd(x["period"].split("~")[-1]) else x["period"],
                          endTime="", region=reg.group(1) if reg else (tag_regions[0][:2] if len(tag_regions) == 1 else "전국"), stage=stage_from(overview) if overview else "확인 필요",
                          cat=x["cat"], target="", tags=d.get("tags", []),
                          kw=kw_of(x["title"] + overview), sections=d.get("sections", [])))

    for x in raw["wevity"]:
        key = "wv:" + x["id"]
        d = det.get(key, {})
        f = d.get("fields", {})
        period = f.get("접수기간", "")
        items.append(dict(id=key, src="위비티", title=x["title"], org=f.get("주최/주관") or x["org"], url=x["url"],
                          apply=f.get("홈페이지"), reg=None, start=ymd(period.split("~")[0]) if "~" in period else None,
                          end=ymd(period.split("~")[-1]), endTime="", region="", stage=d.get("stage", "확인 필요"),
                          cat="공모전", target=f.get("응모대상", ""),
                          kw=sorted(set(kw_of(x["title"]) + d.get("kw", [])), key=KEYWORDS.index),
                          sections=d.get("sections", [])))

    for x in raw["cbnu"]:
        key = "cb:" + x["id"]
        items.append(dict(id=key, src="충북대", title=x["title"], org="충북대학교 창업지원단", url=x["url"],
                          reg=None, start=x["start"], end=x["end"], endTime="", region="충청북도", stage="확인 필요",
                          cat="대학 프로그램", target="", kw=kw_of(x["title"]), sections=[["기간", x["period"]]]))

    for it in items:
        secs = it["sections"]
        if secs and secs[0][0] == "개요" and secs[0][1].split("\n")[0].strip() == it["title"].strip():
            secs[0][1] = "\n".join(secs[0][1].split("\n")[1:]).strip()
        it.pop("tags", None)
        seen.setdefault(it["id"], TODAY)
        it["firstSeen"] = seen[it["id"]]
    return items


def fetch_details(raw, state):
    det = state.setdefault("details", {})
    jobs = [("ks:" + x["id"], ks_detail, x["url"]) for x in raw["kstartup"]]
    jobs += [("bz:" + x["id"], bz_detail, x["url"]) for x in raw["bizinfo"]]
    jobs += [("wv:" + x["id"], wv_detail, x["url"]) for x in raw["wevity"]]
    todo = [j for j in jobs if j[0] not in det]
    print(f"상세 새로 받을 것: {len(todo)}건", flush=True)
    for n, (key, fn, url) in enumerate(todo, 1):
        try:
            det[key] = fn(url)
        except Exception as e:
            print(f"상세 실패 {key}: {e}", flush=True)
        if n % 100 == 0:
            STATE.write_text(json.dumps(state, ensure_ascii=False))
            print(f"  {n}/{len(todo)}", flush=True)
        time.sleep(0.3)
    live = {k for k, _, _ in jobs}
    for k in [k for k in det if k not in live]:  # 목록에서 사라진(마감된) 공고의 상세는 지운다
        del det[k]


if __name__ == "__main__":
    STATE.parent.mkdir(parents=True, exist_ok=True)
    ITEMS.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    raw = {}
    for name, fn in [("kstartup", ks_list), ("bizinfo", bz_list), ("wevity", wv_list), ("cbnu", cbnu_list)]:
        try:
            raw[name] = fn()
        except Exception as e:  # 한 곳이 막혀도 나머지는 계속한다
            print(f"{name} 실패: {e}", flush=True)
            raw[name] = []
        print(f"{name}: {len(raw[name])}건", flush=True)
    fetch_details(raw, state)
    items = build(raw, state)
    state["collectedAt"] = dt.datetime.now(KST).replace(tzinfo=None).isoformat(timespec="minutes")
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    ITEMS.write_text(json.dumps({"collectedAt": state["collectedAt"],
                                 "counts": {k: len(v) for k, v in raw.items()},
                                 "items": items}, ensure_ascii=False))
    print(f"공고 {len(items)}건 → {ITEMS}", flush=True)
