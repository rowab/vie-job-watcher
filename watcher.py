import os, json, time, re, sys, hashlib
from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel
import yaml

# ---------- Utilitaires ----------
def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+","-", s.lower()).strip("-")

def load_seen(path="seen.json") -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()

def save_seen(s: set, path="seen.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(s)), f, ensure_ascii=False, indent=2)

def any_keyword(text: str, keywords: List[str]) -> bool:
    T = text.lower()
    return any(k.lower() in T for k in keywords)

def job_hash(job: Dict[str, Any]) -> str:
    base = (job.get("id") or "") + (job.get("title") or "") + (job.get("url") or "")
    return hashlib.sha1(base.encode("utf-8")).hexdigest()

# ---------- Notifications ----------
def send_telegram(msg: str):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] not configured; msg:", msg)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": msg}, timeout=20)
    except Exception as e:
        print("Telegram error:", e)

def send_email(subject: str, body: str, cfg: dict):
    if not cfg.get("enabled"):
        print("[email] disabled in config")
        return
    import smtplib
    from email.mime.text import MIMEText

    host = cfg["smtp_host"]; port = cfg["smtp_port"]
    user = os.environ.get(cfg["user_env"]); pw = os.environ.get(cfg["pass_env"])
    from_addr = os.environ.get(cfg["from_env"]); to_addr = os.environ.get(cfg["to_env"])

    print(f"[email] host={host}:{port}, from={from_addr}, to={to_addr}, user={user}, pw_set={bool(pw)}")

    if not all([user, pw, from_addr, to_addr]):
        print("[email] missing SMTP env vars"); 
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(from_addr, [to_addr], msg.as_string())
        print("[email] ✅ sent")
    except Exception as e:
        print("[email] ❌ error:", e)


# ---------- Modèle d'offre ----------
class Job(BaseModel):
    id: str
    title: str
    location: Optional[str] = ""
    url: str
    source: str

# ---------- Adaptateurs ----------

def fetch_sanofi_vie(conf) -> List[Job]:
    """
    Appelle l'endpoint Ajax Sanofi:
      GET https://jobs.sanofi.com/fr/search-jobs/results?...
    Le JSON renvoie { "filters": "<html...>", "results": "<html...>" }
    On parse `results` pour extraire: id, titre, location, url.
    On gère la pagination (data-total-pages, data-current-page) avec ?p=2...
    """
    base = conf.get("base", "https://jobs.sanofi.com")
    url  = conf["url"]
    params = conf.get("params", {}).copy()
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "User-Agent": "Mozilla/5.0",
        "Referer": base + "/fr/recherche-d%27offres",  # pas obligatoire mais utile
    }

    jobs: List[Job] = []

    def call(page: int):
        # Deux façons de paginer: param ?p=page OU changer CurrentPage
        p = params.copy()
        p["CurrentPage"] = page
        r = requests.get(url, params=p, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        html = data.get("results", "") or ""
        soup = BeautifulSoup(html, "html.parser")

        # Infos pagination
        section = soup.select_one("section#search-results")
        total_pages = 1
        curr_page = page
        if section:
            try:
                total_pages = int(section.get("data-total-pages", "1"))
                curr_page = int(section.get("data-current-page", str(page)))
            except Exception:
                pass

        # Liste d'offres
        for li in soup.select("#search-results-list ul > li"):
            a = li.select_one("a[data-job-id]")
            if not a: 
                continue
            job_id = a.get("data-job-id", "")
            rel_link = a.get("href", "")
            title_el = a.select_one("h2")
            title = title_el.get_text(strip=True) if title_el else ""
            loc_el = li.select_one(".job-location")
            location = ""
            if loc_el:
                # ex: "Site: Cambridge, Massachusetts" -> on nettoie
                location = loc_el.get_text(" ", strip=True)
                location = location.replace("Site: ", "").strip()
            full_url = rel_link if rel_link.startswith("http") else (base + rel_link)

            jobs.append(Job(
                id=str(job_id),
                title=title,
                location=location,
                url=full_url,
                source="sanofi"
            ))

        return curr_page, total_pages

    # Page 1
    curr, total = call(1)

    # Pages suivantes (s'il y en a)
    for pnum in range(curr + 1, total + 1):
        try:
            # Alternative pagination via ?p=2 (plus robuste sur ce site)
            r = requests.get(url, params={**params, "p": pnum}, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            html = data.get("results", "") or ""
            soup = BeautifulSoup(html, "html.parser")
            for li in soup.select("#search-results-list ul > li"):
                a = li.select_one("a[data-job-id]")
                if not a:
                    continue
                job_id = a.get("data-job-id", "")
                rel_link = a.get("href", "")
                title_el = a.select_one("h2")
                title = title_el.get_text(strip=True) if title_el else ""
                loc_el = li.select_one(".job-location")
                location = ""
                if loc_el:
                    location = loc_el.get_text(" ", strip=True).replace("Site: ", "").strip()
                full_url = rel_link if rel_link.startswith("http") else (base + rel_link)
                jobs.append(Job(
                    id=str(job_id),
                    title=title,
                    location=location,
                    url=full_url,
                    source="sanofi"
                ))
        except Exception as e:
            print(f"[Sanofi] erreur page {pnum}: {e}")

    return jobs






def fetch_greenhouse(company: str) -> List[Job]:
    # https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true
    api = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
    r = requests.get(api, timeout=30)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(Job(
            id=str(j.get("id")),
            title=j.get("title",""),
            location=(j.get("location") or {}).get("name",""),
            url=j.get("absolute_url") or "",
            source="greenhouse"
        ))
    return jobs

def fetch_lever(company: str) -> List[Job]:
    # https://api.lever.co/v0/postings/{company}?mode=json
    api = f"https://api.lever.co/v0/postings/{company}?mode=json"
    r = requests.get(api, timeout=30)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data:
        jobs.append(Job(
            id=str(j.get("id")),
            title=j.get("text",""),
            location=", ".join(j.get("categories", {}).get("location","").split(",")) if j.get("categories") else "",
            url=j.get("hostedUrl") or j.get("applyUrl") or "",
            source="lever"
        ))
    return jobs

def fetch_workday(base_url: str, search_text: str = "VIE") -> List[Job]:
    """
    Valeo / Workday CxS durcit parfois:
      - exige cookies de la page carrière (GET préalable)
      - exige Referer exact
    On fait un GET sur la page carrière pour obtenir les cookies, puis on POST.
    On essaie 4 variantes: searchText, appliedFacets VIE, sans filtre, puis GET avec appliedFacets.
    """
    from urllib.parse import urlsplit
    parts = urlsplit(base_url)
    root = f"{parts.scheme}://{parts.netloc}"
    segs = [p for p in parts.path.split("/") if p]
    # .../wday/cxs/<tenant>/<site>/jobs -> on veut /<site>
    site_slug = segs[-2] if len(segs) >= 2 else ""
    referer = f"{root}/{site_slug}" if site_slug else root

    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"

    s = requests.Session()
    # 1) GET pour cookies
    try:
        s.get(referer, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        }, timeout=30)
    except Exception as e:
        print("[Workday] GET referer error:", e)

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "User-Agent": UA,
        "Origin": root,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
    }

    # ID facette Workday pour "Volontariat international en entreprise (VIE)" (vu dans ta réponse)
    VIE_FACET_ID = "1bc7ee912dc9100bd4a826d6e65d0000"

    payloads = [
        {"limit": 100, "offset": 0, "searchText": search_text},  # A
        {"limit": 100, "offset": 0, "appliedFacets": {"workerSubType": [VIE_FACET_ID]}},  # B
        {"limit": 100, "offset": 0},  # C (on filtrera par mots-clés ensuite)
    ]

    data = None
    last_err = None

    # 2) POST avec 3 payloads possibles
    for p in payloads:
        try:
            r = s.post(base_url, json=p, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            try:
                print(f"[Workday debug] HTTP {getattr(r,'status_code', '??')} body: {getattr(r,'text','')[:400]}")
            except Exception:
                pass
            last_err = e

    # 3) Ultime essai: GET avec appliedFacets=workerSubType:<ID>
    if data is None:
        try:
            params = {"limit": 100, "offset": 0, "appliedFacets": f"workerSubType:{VIE_FACET_ID}"}
            r = s.get(base_url, params=params, headers={"Accept": "application/json", "User-Agent": UA, "Referer": referer}, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            last_err = e

    if data is None:
        print(f"[Workday] ❌ {last_err}")
        return []

    # ---- mapping robuste ----
    jobs: List[Job] = []
    for j in data.get("jobPostings", []):
        job_id = j.get("id")
        bf = j.get("bulletFields")
        if not job_id and isinstance(bf, list) and bf:
            job_id = bf[0]
        if not job_id:
            ep = (j.get("externalPath") or "")
            m = re.search(r"(REQ\d+)", ep)
            job_id = m.group(1) if m else (ep or j.get("title", ""))

        external_path = j.get("externalPath") or ""
        url = j.get("externalUrl") or (root + external_path)

        loc = j.get("locationsText") or ""
        if not loc:
            locs = j.get("locations")
            if isinstance(locs, list):
                loc = ", ".join(locs)
            elif isinstance(locs, str):
                loc = locs

        jobs.append(Job(
            id=str(job_id),
            title=(j.get("title") or "").strip(),
            location=loc,
            url=url,
            source="valeo"
        ))
    return jobs


def fetch_workday_raw(conf: Dict[str, Any]) -> List[Job]:
    """
    Poste EXACTEMENT le body fourni dans config.yml sur /wday/cxs/.../jobs.
    Fait d'abord un GET sur la page du site carrière pour récupérer les cookies.
    """
    from urllib.parse import urlsplit
    base_url = conf["base_url"]
    body = conf.get("body", {})
    limit = body.get("limit", 20)

    parts = urlsplit(base_url)
    root = f"{parts.scheme}://{parts.netloc}"
    segs = [p for p in parts.path.split("/") if p]
    site_slug = segs[-2] if len(segs) >= 2 else ""
    referer = f"{root}/{site_slug}" if site_slug else root

    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"

    s = requests.Session()
    # 1) Cookies
    try:
        s.get(referer, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        }, timeout=30)
    except Exception as e:
        print("[Workday/raw] GET referer error:", e)

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "User-Agent": UA,
        "Origin": root,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
    }

    # 2) POST exact
    r = s.post(base_url, json=body, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

    # 3) Mapping robuste
    jobs: List[Job] = []
    for j in data.get("jobPostings", []):
        job_id = j.get("id")
        bf = j.get("bulletFields")
        if not job_id and isinstance(bf, list) and bf:
            job_id = bf[0]
        if not job_id:
            ep = (j.get("externalPath") or "")
            m = re.search(r"(REQ\d+)", ep)
            job_id = m.group(1) if m else (ep or j.get("title", ""))

        external_path = j.get("externalPath") or ""
        url = j.get("externalUrl") or (root + external_path)

        loc = j.get("locationsText") or ""
        if not loc:
            locs = j.get("locations")
            if isinstance(locs, list): loc = ", ".join(locs)
            elif isinstance(locs, str): loc = locs

        jobs.append(Job(
            id=str(job_id),
            title=(j.get("title") or "").strip(),
            location=loc,
            url=url,
            source=conf.get("source", "workday")
        ))
    return jobs

def fetch_oracle_orc(conf: Dict[str, Any]) -> List[Job]:
    """Oracle Recruiting Cloud (ORC) via recruitingCEJobRequisitions + finder=..."""
    url = conf["base_url"]
    base_params = (conf.get("params") or {}).copy()
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

    limit = int(base_params.get("limit", 50))
    offset0 = int(base_params.get("offset", 0))

    jobs: List[Job] = []
    offset = offset0
    while True:
        params = base_params.copy()
        params["offset"] = offset
        params["limit"] = limit

        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()

        # ORC renvoie souvent items[] avec, parfois, un sous-tableau requisitionList[]
        items = data.get("items", []) or []
        recs = []
        for it in items:
            if isinstance(it.get("requisitionList"), list):
                recs.extend(it["requisitionList"])
            else:
                recs.append(it)

        for it in recs:
            jid = (it.get("Id") or it.get("JobRequisitionId") or
                   it.get("RequisitionNumber") or it.get("Number") or "")
            title = (it.get("PostingTitle") or it.get("Title") or it.get("Name") or "").strip()

            loc = (it.get("PrimaryLocationFullName") or it.get("PrimaryLocationName") or
                   it.get("Location") or "")
            if not loc:
                city = it.get("PrimaryLocationCity") or ""
                state = it.get("PrimaryLocationState") or ""
                country = it.get("PrimaryLocationCountry") or ""
                loc = ", ".join([x for x in (city, state, country) if x])

            url_ext = (it.get("ExternalURL") or it.get("ExternalUrl") or it.get("jobPostingUrl") or "")
            if not url_ext:
                for L in (it.get("links") or []):
                    if L.get("href"):
                        url_ext = L["href"]; break

            jobs.append(Job(
                id=str(jid or title),
                title=title or "(sans titre)",
                location=loc,
                url=url_ext,
                source=conf.get("source", "oracle_orc")
            ))

        # pagination
        has_more = bool(data.get("hasMore"))
        if not has_more or len(recs) < limit:
            break
        offset += limit

    return jobs




def fetch_json_api(conf: Dict[str, Any]) -> List[Job]:
    """
    Générique pour des endpoints JSON (Phenom/SuccessFactors custom/etc).
    conf attend: url, method, params/body, mapping {id,title,location,url}, source?
    """
    url = conf["url"]; method = conf.get("method","GET").upper()
    params = conf.get("params", {})
    body = conf.get("body", {})
    headers = conf.get("headers", {})
    timeout = conf.get("timeout", 30)
    if method == "GET":
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
    else:
        r = requests.request(method, url, json=body or None, params=params or None, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    # Chemin de la liste (facultatif). Si absent, on suppose que `data` EST la liste.
    items = data
    json_path = conf.get("json_path_items")
    if json_path:
        # mini json-path "$.foo.bar" -> on fait simple
        path = json_path.strip("$.").split(".")
        for p in path:
            items = items.get(p, {})

    mapping = conf.get("mapping", {})
    src = conf.get("source", "json_api")

    jobs = []
    if isinstance(items, list):
        iterable = items
    elif isinstance(items, dict):
        iterable = items.values()
    else:
        iterable = []

    for it in iterable:
        try:
            jobs.append(Job(
                id=str(get_nested(it, mapping.get("id"))),
                title=str(get_nested(it, mapping.get("title")) or ""),
                location=str(get_nested(it, mapping.get("location")) or ""),
                url=str(get_nested(it, mapping.get("url")) or ""),
                source=src
            ))
        except Exception:
            continue
    return jobs

def get_nested(obj, dotted_key: Optional[str]):
    if not dotted_key:
        return None
    cur = obj
    for k in dotted_key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
    return cur

# ---------- Orchestrateur ----------
def main():
    with open("config.yml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    seen = load_seen()
    new_seen = set(seen)
    found = []

    keywords = cfg.get("keywords", ["VIE"])
    notify_cfg = cfg.get("notify", {})
    sites = cfg.get("sites", [])

    for site in sites:
        stype = site.get("type")
        sname = site.get("name","(site)")
        print(f"Checking: {sname} [{stype}]")

        try:
            if stype == "greenhouse":
                jobs = fetch_greenhouse(site["company"])
            elif stype == "lever":
                jobs = fetch_lever(site["company"])
            elif stype == "workday":
                # On peut passer search_text="VIE" pour ne ramener que l'utile
                jobs = fetch_workday(site["base_url"], search_text="VIE")
            elif stype == "json_api":
                jobs = fetch_json_api(site)
            elif stype == "sanofi_vie":
                jobs = fetch_sanofi_vie(site)
            elif stype == "workday_raw":
                jobs = fetch_workday_raw(site)
            elif stype == "oracle_orc":
                jobs = fetch_oracle_orc(site)

            else:
                print(f"Type inconnu: {stype}")
                jobs = []
        except Exception as e:
            print(f"[{sname}] erreur: {e}")
            continue

        for j in jobs:
            text = f"{j.title} {j.location} {j.url}"
            if any_keyword(text, keywords):
                h = job_hash(j.dict())
                if h not in new_seen:
                    new_seen.add(h)
                    found.append(j)

    # Notifications
    if found:
        lines = [f"🆕 {len(found)} nouvelle(s) offre(s) VIE détectée(s):"]
        for j in found:
            lines.append(f"- {j.title} — {j.location} [{j.source}]\n{j.url}")
        msg = "\n".join(lines)

        if notify_cfg.get("telegram", {}).get("enabled"):
            send_telegram(msg)

        if notify_cfg.get("email", {}).get("enabled"):
            send_email("Nouvelles offres VIE détectées", msg, notify_cfg["email"])

        print(msg)
    else:
        print("Aucune nouvelle offre VIE.")

    save_seen(new_seen)

if __name__ == "__main__":
    main()
