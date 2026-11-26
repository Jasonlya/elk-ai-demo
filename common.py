"""公共工具：环境变量加载 + ES/Kibana 请求封装。"""
import json
import os
import time
from urllib.parse import urlsplit

import requests


def load_env(path=".env"):
    """极简 .env 解析，省掉 python-dotenv 依赖。已有的环境变量优先。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env()

ES_URL = os.getenv("ES_URL", "http://localhost:9200").rstrip("/")
KIBANA_URL = os.getenv("KIBANA_URL", "http://localhost:5601").rstrip("/")
ES_API_KEY = os.getenv("ES_API_KEY", "").strip()
KIBANA_API_KEY = os.getenv("KIBANA_API_KEY", "").strip() or ES_API_KEY

INDEX_PATTERN = "app-logs-*"
DATA_VIEW = "app-logs"


def _headers(api_key, extra=None):
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"ApiKey {api_key}"
    if extra:
        h.update(extra)
    return h


def es(method, path, body=None, expect=200):
    url = f"{ES_URL}/{path.lstrip('/')}"
    r = requests.request(method, url, headers=_headers(ES_API_KEY),
                         data=json.dumps(body) if body is not None else None, timeout=120)
    if r.status_code >= 300 and r.status_code != expect:
        raise RuntimeError(f"ES {method} {path} -> {r.status_code}: {r.text[:500]}")
    try:
        return r.json()
    except ValueError:
        return r.text


def es_bulk(ndjson, refresh=False):
    url = f"{ES_URL}/_bulk" + ("?refresh=true" if refresh else "")
    r = requests.post(url, headers=_headers(ES_API_KEY,
                      {"Content-Type": "application/x-ndjson"}),
                      data=ndjson.encode("utf-8"), timeout=180)
    if r.status_code >= 300:
        raise RuntimeError(f"bulk -> {r.status_code}: {r.text[:500]}")
    rj = r.json()
    if rj.get("errors"):
        for item in rj["items"]:
            err = item.get("index", {}).get("error")
            if err:
                raise RuntimeError(f"bulk item error: {err}")
    return rj


def kb(method, path, body=None):
    """Kibana API 必须带 kbn-xsrf，否则会被拒。"""
    url = f"{KIBANA_URL}/{path.lstrip('/')}"
    r = requests.request(method, url, headers=_headers(KIBANA_API_KEY, {"kbn-xsrf": "true"}),
                         data=json.dumps(body) if body is not None else None, timeout=120)
    if r.status_code >= 300:
        raise RuntimeError(f"Kibana {method} {path} -> {r.status_code}: {r.text[:500]}")
    try:
        return r.json()
    except ValueError:
        return r.text


def wait_ready(url, name=None, timeout=300):
    """等 ES / Kibana 起来，比 sleep 固定秒数靠谱。按端口区分探活方式。"""
    name = name or url
    is_kibana = urlsplit(url).port == 5601
    probe = f"{url}/api/status" if is_kibana else f"{url}/_cluster/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(probe, headers=_headers(KIBANA_API_KEY if is_kibana else ES_API_KEY), timeout=5)
            if r.status_code == 200:
                if not is_kibana:
                    return True
                level = r.json().get("status", {}).get("overall", {}).get("level")
                if level == "available":
                    return True
        except requests.RequestException:
            pass
        time.sleep(3)
    raise TimeoutError(f"{name} 在 {timeout}s 内没起来，检查内存/端口占用")
