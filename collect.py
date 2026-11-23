"""지원사업 공고 수집기.

K-Startup(진행 중 공고와 상세), 기업마당(진행 중 공고와 상세), 위비티(취업/창업 공모전과 상세),
충북대 창업지원단(프로그램 목록), 그 밖의 기관(MORE 표, 목록만)을 공개 웹 페이지에서 읽어 사이트용 데이터를 만든다.
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
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).parent
STATE = ROOT / "data" / "state.json"
ITEMS = ROOT / "site" / "data" / "items.json"
UA = {"User-Agent": "Mozilla/5.0 (meaningworld support-board; contact meaninglens0@gmail.com)"}
KST = ZoneInfo("Asia/Seoul")
TODAY = dt.datetime.now(KST).date().isoformat()

KS = "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do"
BZ = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do"  # 2026-11 옛 주소(/web/lay1/...list.do)는 403
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
def bz_page(page):
    h = get(f"{BZ}?rows=15&cpage={page}&schEndAt=N")
    out = []
    for r in re.findall(r"<tr>(.*?)</tr>", h[h.find("<tbody"):h.find("</tbody>")], re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        m = re.search(r"pblancId=(PBLN_\d+)", r)
        if len(tds) >= 7 and m:
            out.append(dict(id=m.group(1), cat=text(tds[1]), title=text(tds[2]), period=text(tds[3]),
                            ministry=text(tds[4]), org=text(tds[5]), reg=text(tds[6]), url=BZD + m.group(1)))
    return out


def bz_list():
    # 해외(GitHub 서버)에서는 한 쪽에 3~4초 걸려 95쪽이 6분이다. 4쪽씩 동시에 읽는다.
    items = {}
    with ThreadPoolExecutor(4) as pool:
        for first in range(1, 200, 4):
            pages = list(pool.map(bz_page, range(first, first + 4)))
            new = 0
            for rows in pages:
                for r in rows:
                    new += r["id"] not in items
                    items[r["id"]] = r
            if new == 0 or any(len(rows) < 15 for rows in pages):
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


# ---------- 그 밖의 기관: 목록만 읽는다(상세 안 받음) ----------
NOT_NOTICE = re.compile(r"채용|결과|합격|최종\s?선발|선정\s?(안내|명단)|발표|휴관|휴무|운영 종료|입찰|낙찰|용역")
RECENT_DAYS = 60


def _alive(end, reg):
    """마감을 알면 마감 안 지난 것, 모르면 등록일이 60일 안인 것."""
    if end:
        return end >= TODAY
    cut = (dt.date.fromisoformat(TODAY) - dt.timedelta(days=RECENT_DAYS)).isoformat()
    return bool(reg) and reg >= cut


def _item(id, title, org, url, reg=None, start=None, end=None, period=""):
    return dict(id=str(id), title=title, org=org, url=url, reg=reg, start=start, end=end, period=period)


def _title_end(title):
    """제목의 '~11.27', '(~10/29)' 꼴 마감일. 올해 기준, 반년 넘게 지난 날짜면 내년으로 본다."""
    m = re.search(r"~\s*(\d{1,2})\s*[./]\s*(\d{1,2})", title)
    if not m:
        return None
    today = dt.date.fromisoformat(TODAY)
    d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
    if (today - d).days > 180:
        d = d.replace(year=today.year + 1)
    return d.isoformat()


# ---------- 충북창업스타티움 (https://startium.co.kr) ----------
# 교육신청(code=15)은 전부 '종료' 표시, 모집 공고는 공지사항(code=18)에 [홍보]/[참가자 모집] 머리말로 올라온다.
# 2026-12-16 운영 종료 예정(공지 145번).
def startium_list():
    base = "https://startium.co.kr/sub.php"
    out = {}
    for page in (1, 2):
        h = get(f"{base}?code=18&page={page}")
        for r in re.findall(r"<tr>(.*?)</tr>", h[h.find("<tbody"):], re.S):
            a = re.search(r'href="\?code=18&amp;mode=view&amp;no=(\d+)[^"]*"[^>]*>(.*?)</a>', r, re.S)
            d = ymd(re.search(r"\d{4}-\d{2}-\d{2}", r).group(0)) if re.search(r"\d{4}-\d{2}-\d{2}", r) else None
            if not a:
                continue
            title = text(a.group(2))
            end = _title_end(title)
            if NOT_NOTICE.search(title) or not _alive(end, d):
                continue
            out[a.group(1)] = _item("n" + a.group(1), title, "충북창업스타티움",
                                    f"{base}?code=18&mode=view&no={a.group(1)}", reg=d, end=end)
        time.sleep(0.4)
    h = get(f"{base}?code=15")  # 종료 딱지(<i class="close">)가 없는 교육만
    for li in re.findall(r'<li>\s*<div class="con_box">(.*?)</li>', h, re.S):
        if 'class="close"' in li:
            continue
        no = re.search(r"no=(\d+)", li).group(1)
        out["p" + no] = _item("p" + no, text(re.search(r'p_title">(.*?)</p>', li, re.S).group(1)), "충북창업스타티움",
                              f"{base}?code=15&mode=view&no={no}")
    return list(out.values())


# ---------- 충북콘텐츠코리아랩 (https://www.cbckl.kr) ----------
def cbckl_list():
    base = "https://www.cbckl.kr/www/"
    out = []
    for page in (1, 2, 3):
        h = get(f"{base}selectLabProgrmList.do?key=72&pageIndex={page}")
        for r in re.findall(r"<tr>(.*?)</tr>", h[h.find("<tbody"):], re.S):
            a = re.search(r'selectLabProgrmView\.do\?[^"]*labProgrmNo=(\d+)"[^>]*>(.*?)</a>', r, re.S)
            p = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", r)
            if not (a and p):
                continue
            title = text(a.group(2))
            if "신청마감" in text(r) or p.group(2) < TODAY:
                continue
            out.append(_item(a.group(1), title, "충북콘텐츠코리아랩",
                             f"{base}selectLabProgrmView.do?key=72&labProgrmNo={a.group(1)}",
                             start=p.group(1), end=p.group(2), period=f"{p.group(1)} ~ {p.group(2)}"))
        time.sleep(0.4)
    return out


# ---------- 정보통신산업진흥원 NIPA (https://www.nipa.kr/home/2-2 사업공고) ----------
def nipa_list():
    out = []
    for page in (1, 2, 3):
        h = get(f"https://www.nipa.kr/home/2-2?curPage={page}")
        for r in re.findall(r"<tr>(.*?)</tr>", re.sub(r"<!--.*?-->", "", h[h.find("<tbody"):], flags=re.S), re.S):
            a = re.search(r'href="(/home/2-2/(\d+))"[^>]*>(.*?)</a>', r, re.S)
            if not a or "종료" in text(re.search(r'class="point[^"]*">(.*?)</div>', r, re.S).group(1) if re.search(r'class="point', r) else ""):
                continue
            p = re.search(r"신청기간\s*:\s*(\d{4}-\d{2}-\d{2})[^~]*~\s*(\d{4}-\d{2}-\d{2})", text(r))
            biz = re.search(r'class="box[^"]*">(.*?)</span>', r, re.S)
            end = p.group(2) if p else None
            if end and end < TODAY:
                continue
            out.append(_item(a.group(2), text(a.group(3)), "NIPA" + (f" {text(biz.group(1))}" if biz else ""),
                             "https://www.nipa.kr" + a.group(1), reg=ymd(text(r.split("</td>")[-2])) if r.count("</td>") > 1 else None,
                             start=p.group(1) if p else None, end=end,
                             period=f"{p.group(1)} ~ {p.group(2)}" if p else ""))
        time.sleep(0.4)
    return out


# ---------- 충북과학기술혁신원 (https://www.cbist.or.kr) 사업공고 1131 + 타기관공고 1132 ----------
def cbist_list():
    base = "https://www.cbist.or.kr/home/sub.do"
    out = {}
    for mncd, org in ((1131, "충북과학기술혁신원"), (1132, "충북과학기술혁신원(타기관공고)")):
        for page in (1, 2):
            h = get(f"{base}?mncd={mncd}&page={page}")
            for r in re.findall(r"<tr>(.*?)</tr>", h[h.find("<tbody"):h.find("</tbody>")], re.S):
                a = re.search(r'no=(\d+)[^"]*"[^>]*class="title"[^>]*>(.*?)</a>', r, re.S)
                d = [text(x) for x in re.findall(r'class="board_date">(.*?)</td>', r, re.S)]
                if not (a and len(d) >= 2):
                    continue
                ds = re.findall(r"\d{4}-\d{2}-\d{2}", d[1])
                start, end = (ds[0], ds[-1]) if ds else (None, None)
                if _alive(end, d[0]) and not NOT_NOTICE.search(text(a.group(2))):
                    out[a.group(1)] = _item(a.group(1), text(a.group(2)), org, f"{base}?mncd={mncd}&mode=view&no={a.group(1)}",
                                            reg=d[0], start=start, end=end, period=d[1])
            time.sleep(0.4)
    return list(out.values())


# ---------- 청주 청년뜨락5959 (https://www.youthspace5959.com) 청년지원사업 ----------
def youth5959_list():
    base = "https://www.youthspace5959.com"
    out = []
    for page in (1, 2):
        h = get(f"{base}/pssrp/list.do?key=1907297637375&pageIndex={page}")
        for no, li in re.findall(r"<li>\s*<a href=\"#none\" onclick=\"goView\('(\d+)'\);\">(.*?)</li>", h, re.S):
            ds = re.findall(r"(\d{2})\.(\d{2})\.(\d{2})", re.search(r'class="r_date">(.*?)</span>', li, re.S).group(1))
            ds = [f"20{y}-{m}-{d}" for y, m, d in ds]
            title = text(re.search(r'class="tit">(.*?)</span>', li, re.S).group(1))
            end = ds[-1] if ds else None
            if not _alive(end, None) or not end:
                continue
            out.append(_item(no, title, "청주 청년뜨락5959", f"{base}/pssrp/view.do?key=1907297637375&pssrpMngKey={no}",
                             start=ds[0], end=end, period=f"{ds[0]} ~ {end}"))
        time.sleep(0.4)
    return out


# ---------- 학생 창업유망팀 300+ (https://u300.kr) 공개 JSON API ----------
def u300_list():
    api = "https://user-api.u300.kr/api/board/query/"
    out = {}
    for page in (1, 2):
        j = json.loads(get(f"{api}get-notice-list?page={page}"))["data"]["content"]
        for n in j:
            title, reg = n["noticeTitle"], ymd(n["createdAt"])
            if re.search(r"모집|공모|신청|접수", title) and not NOT_NOTICE.search(title) and _alive(None, reg):
                out[n["noticeNo"]] = _item(n["noticeNo"], title, "학생 창업유망팀 300+",
                                           f"https://u300.kr/board/notice/detail/{n['noticeNo']}", reg=reg)
        time.sleep(0.4)
    for p in json.loads(get(f"{api}program-list"))["data"]["content"]:
        s, e = ymd(p["programRecruitStartDate"]), ymd(p["programRecruitEndDate"])
        if e and e >= TODAY:
            out["p%s" % p["programNo"]] = _item("p%s" % p["programNo"], p["programTitle"], "학생 창업유망팀 300+",
                                                f"https://u300.kr/board/program/detail/{p['programNo']}", start=s, end=e,
                                                period=f"{s} ~ {e}")
    return list(out.values())


# ---------- 넥스트유니콘 지원프로그램 (https://www.nextunicorn.kr/support-programs) 사이트가 쓰는 GraphQL ----------
NU_Q = ("query($p:Int!){supportPrograms(pageNumber:$p,filter:{establishmentPeriodCodes:[],qualificationCodes:[],"
        "applicationSectionIds:[],query:null}){supportPrograms{id hostName title applyStartAt applyEndAt "
        "applicationSections} canApplyProgramCount}}")


def _nu_page(p):
    req = urllib.request.Request("https://gql.nextunicorn.kr/", json.dumps({"query": NU_Q, "variables": {"p": p}}).encode(),
                                 {**UA, "content-type": "application/json"})
    for _ in range(3):  # 가끔 data가 null로 온다
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())["data"]
        if d and d["supportPrograms"]:
            return d["supportPrograms"]
        time.sleep(1)
    return {"supportPrograms": [], "canApplyProgramCount": 0}


def _kst(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(KST).date().isoformat() if s else None


def nextunicorn_list():
    # 접수 중인 것이 마감 임박순으로 먼저 나오고 그 뒤에 마감된 것이 온다. 접수 중 건수(canApplyProgramCount)까지만 읽는다.
    out, page = {}, 1
    while True:
        d = _nu_page(page)
        before = len(out)
        for x in d["supportPrograms"]:
            end = _kst(x["applyEndAt"])
            if end and end < TODAY:
                continue
            out[x["id"]] = _item(x["id"], x["title"], x["hostName"], f"https://www.nextunicorn.kr/support-programs/{x['id']}",
                                 start=_kst(x["applyStartAt"]), end=end, period=f"{_kst(x['applyStartAt']) or ''} ~ {end or ''}")
        if len(out) == before or len(out) >= d["canApplyProgramCount"] or page >= 30:  # 한 쪽에 접수 중이 하나도 없으면 끝
            return list(out.values())
        page += 1
        time.sleep(0.4)


# ---------- 창업보육센터 BI-Net (옛 bi.go.kr -> https://www.smes.go.kr/binet) ----------
# 지원사업공고(ANNOUNCE)는 전국, 입주기업모집(ENTERPRISERECRUIT)은 충청북도 센터만. 목록 제목이 20자쯤에서 '..'로 잘린다.
def binet_list():
    base = "https://www.smes.go.kr/binet/board/"
    out = {}
    for code, label in (("ANNOUNCE", "지원사업공고"), ("ENTERPRISERECRUIT", "입주기업모집")):
        for page in range(3):
            h = get(f"{base}list.do?boardID=RECRUIT&frefaceCode={code}&pager.offset={15 * page}")
            for r in re.findall(r"<tr>(.*?)</tr>", h[h.find("<tbody"):h.find("</tbody>")], re.S):
                a = re.search(r"fncEditView\((\d+),(\d+),", r)
                tds = [text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
                p = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", r)
                if not (a and p and len(tds) >= 4):
                    continue
                region, body = tds[1], text(re.sub(r'<span class="ico_[^>]*>.*?</span>', "", r.split('class="al">')[1].split("</td>")[0], flags=re.S))
                org = re.match(r"\[([^\]]+)\]", body)
                if p.group(2) < TODAY or (code == "ENTERPRISERECRUIT" and region != "충청북도") or NOT_NOTICE.search(body):
                    continue
                out[a.group(1)] = _item(a.group(1), body, f"{org.group(1) if org else 'BI-Net'} ({label})",
                                        f"{base}editView.do?boardID=RECRUIT&frefaceCode={code}&boardVO.postSeq={a.group(1)}"
                                        f"&boardVO.registDate={a.group(2)}&boardVO.viewFlag=view",
                                        reg=ymd(f"{a.group(2)[:4]}-{a.group(2)[4:6]}-{a.group(2)[6:8]}"), start=p.group(1), end=p.group(2),
                                        period=f"{p.group(1)} ~ {p.group(2)}")
            time.sleep(0.4)
    return list(out.values())


# SKIPPED (robots.txt가 User-agent: * 전체 차단이라 수집하지 않음): 충북창조경제혁신센터(ccei), 충북테크노파크(cbtp),
# 충청북도기업진흥원(cba), 한국사회적기업진흥원(socialenterprise), 한국콘텐츠진흥원(kocca 지원공고 list.do 차단).
# 진행 공고가 사실상 없어 뺌: 아산나눔재단(공지 목록에 날짜 없음, 모집은 대부분 마감), 한국청년기업가정신재단(행사·교원 대상).


# 출처 키 -> (화면 이름, 지역). 지역이 ""이면 전국·충북 거르기에 걸리지 않는다.
MORE = {"startium": ("충북창업스타티움", "충청북도"), "cbckl": ("충북콘텐츠코리아랩", "충청북도"),
        "cbist": ("충북과학기술혁신원", "충청북도"), "youth5959": ("청년뜨락5959", "충청북도"),
        "u300": ("학생 창업유망팀 300", "전국"), "nipa": ("NIPA", "전국"),
        "nextunicorn": ("넥스트유니콘", ""), "binet": ("창업보육센터", "")}
LABELS = {"kstartup": "K-Startup", "bizinfo": "기업마당", "wevity": "위비티", "cbnu": "충북대"}


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

    norm = lambda t: re.sub(r"\W", "", t)
    known = {norm(it["title"]) for it in items}  # 넥스트유니콘 등이 다시 실은 공고는 원래 출처 쪽만 남긴다
    for key, (label, region) in MORE.items():
        for x in raw[key]:
            if norm(x["title"]) in known:
                continue
            known.add(norm(x["title"]))
            items.append(dict(id=f"{key}:{x['id']}", src=label, title=x["title"], org=x["org"], url=x["url"],
                              reg=x["reg"], start=x["start"], end=x["end"], endTime="", region=region,
                              stage=stage_from(x["title"]), cat="", target="", kw=kw_of(x["title"]),
                              sections=[["기간", x["period"]]] if x["period"].strip(" ~") else []))

    for it in items:
        secs = it["sections"]
        if secs and secs[0][0] == "개요" and secs[0][1].split("\n")[0].strip() == it["title"].strip():
            secs[0][1] = "\n".join(secs[0][1].split("\n")[1:]).strip()
        it.pop("tags", None)
        if it.get("apply") and not re.match(r"https?://[\w-]+(\.[\w-]+)+([/?#:]|$)", it["apply"]):
            it["apply"] = None  # 공고문이 링크 칸에 글('첨부파일' 등)을 넣은 경우
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
    for name, fn in [("kstartup", ks_list), ("bizinfo", bz_list), ("wevity", wv_list), ("cbnu", cbnu_list)] + \
            [(k, globals()[k + "_list"]) for k in MORE]:
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
                                 "counts": {LABELS.get(k) or MORE[k][0]: len(v) for k, v in raw.items()},
                                 "items": items}, ensure_ascii=False))
    print(f"공고 {len(items)}건 → {ITEMS}", flush=True)
