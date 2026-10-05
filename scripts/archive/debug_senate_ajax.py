"""Extract DataTables JavaScript configuration from cached search page HTML."""

import re
import time
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

SENATE_BASE_URL = "https://efdsearch.senate.gov"

session = cffi_requests.Session(impersonate="chrome131")

# Agreement flow
resp = session.get(f"{SENATE_BASE_URL}/search/home/")
soup = BeautifulSoup(resp.text, "lxml")
csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
csrf_token = csrf_input["value"] if csrf_input else ""

time.sleep(0.5)
resp = session.post(
    f"{SENATE_BASE_URL}/search/home/",
    data={"csrfmiddlewaretoken": csrf_token, "prohibition_agreement": "1"},
    headers={"Referer": f"{SENATE_BASE_URL}/search/home/", "Origin": SENATE_BASE_URL},
    allow_redirects=True,
)

if "csrftoken" in session.cookies:
    csrf_token = session.cookies["csrftoken"]

# Search form POST
time.sleep(0.5)
resp = session.post(
    f"{SENATE_BASE_URL}/search/",
    data={
        "csrfmiddlewaretoken": csrf_token,
        "first_name": "", "last_name": "",
        "filer_type": "1", "report_type": "11",
        "submitted_start_date": "01/01/2025",
        "submitted_end_date": "04/15/2026",
    },
    headers={"Referer": f"{SENATE_BASE_URL}/search/", "Origin": SENATE_BASE_URL},
)

# Extract all scripts
html = resp.text
soup = BeautifulSoup(html, "lxml")
scripts = soup.find_all("script")

for i, script in enumerate(scripts):
    text = script.get_text()
    if "DataTable" in text or "dataTable" in text or "filedReports" in text:
        print(f"\n{'='*80}")
        print(f"Script block {i} - DataTables config:")
        print(f"{'='*80}")
        print(text.strip())
