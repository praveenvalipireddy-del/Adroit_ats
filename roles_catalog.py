"""Role picker (Jobs and the consultant's Matched jobs): categories -> roles, each role matched on job TITLES.

A role is a label plus the title phrases that mean it (whole words: "AI Engineer" never matches "Domain").
counts() says how many of the given jobs each role and category has, so the picker shows real numbers.
"""
import re
from typing import Dict, Iterable, List

CATALOG = [
    ("Software, Internet & AI", [
        ("Software Engineer", ["software engineer", "software developer", "sde", "programmer"]),
        ("Java Developer", ["java developer", "java engineer", "java full stack", "java"]),
        ("Python Developer", ["python developer", "python engineer"]),
        (".NET Developer", [".net developer", ".net engineer", "c# developer", "dotnet"]),
        ("Full Stack Developer", ["full stack", "fullstack", "full-stack"]),
        ("Frontend Developer", ["frontend", "front end", "front-end", "react developer", "angular developer", "ui developer", "javascript", "typescript"]),
        ("Backend Developer", ["backend", "back end", "back-end", "node.js developer", "golang developer"]),
        ("Mobile Developer", ["android developer", "ios developer", "mobile developer", "flutter developer", "react native"]),
        ("AI / ML Engineer", ["ai engineer", "ml engineer", "machine learning", "ai/ml", "genai", "generative ai", "llm", "ai developer", "ai automation"]),
        ("Data Scientist", ["data scientist", "data science"]),
    ]),
    ("Data & Analytics", [
        ("Data Analyst", ["data analyst", "analytics analyst", "reporting analyst"]),
        ("Data Engineer", ["data engineer", "etl developer", "big data", "databricks engineer", "spark developer"]),
        ("Business Analyst", ["business analyst", "business systems analyst"]),
        ("BI / Power BI Developer", ["power bi", "bi developer", "tableau developer", "business intelligence", "looker"]),
        ("Database Administrator", ["database administrator", "dba", "sql server", "oracle dba", "database engineer"]),
    ]),
    ("Cloud & DevOps", [
        ("DevOps Engineer", ["devops", "dev ops", "site reliability", "sre", "platform engineer"]),
        ("Cloud Engineer", ["cloud engineer", "aws engineer", "azure engineer", "gcp engineer", "cloud architect", "cloud developer"]),
        ("Network / Systems Engineer", ["network engineer", "systems engineer", "system administrator", "linux administrator", "infrastructure engineer"]),
    ]),
    ("QA & Testing", [
        ("QA / Test Engineer", ["qa engineer", "quality assurance", "test engineer", "tester", "sdet", "qa analyst"]),
        ("Automation Tester", ["automation tester", "test automation", "selenium", "qa automation", "automation qa"]),
    ]),
    ("Cybersecurity", [
        ("Security Engineer", ["security engineer", "cybersecurity", "cyber security", "information security", "security analyst", "soc analyst",
                               "penetration tester", "iam engineer"]),
    ]),
    ("SAP, Salesforce & ERP", [
        ("SAP Consultant", ["sap"]),
        ("Salesforce Developer / Admin", ["salesforce"]),
        ("ServiceNow Developer", ["servicenow"]),
        ("Oracle / Workday / ERP", ["oracle fusion", "oracle ebs", "workday", "erp", "dynamics 365", "netsuite"]),
    ]),
    ("Product & Project", [
        ("Product Manager", ["product manager", "product owner"]),
        ("Project / Program Manager", ["project manager", "program manager", "delivery manager", "pmo", "scrum master"]),
    ]),
    ("Marketing", [
        ("Marketing Manager", ["marketing manager", "marketing lead", "head of marketing"]),
        ("Marketing Operations", ["marketing operations", "marketing ops", "marketing automation", "campaign manager"]),
        ("Digital Marketing / SEO", ["digital marketing", "seo", "sem", "performance marketing", "social media", "growth marketing"]),
        ("Content / Brand", ["content writer", "content marketing", "brand manager", "copywriter", "communications"]),
    ]),
    ("Sales & Business Development", [
        ("Sales / Account Executive", ["account executive", "sales executive", "sales manager", "inside sales", "sales representative"]),
        ("Business Development", ["business development", "bdr", "sdr", "partnerships"]),
        ("Customer Success", ["customer success", "account manager"]),
    ]),
    ("Finance & Accounting", [
        ("Accountant / Finance", ["accountant", "accounting", "financial analyst", "finance manager", "controller", "fp&a", "audit", "tax"]),
    ]),
    ("HR & Recruiting", [
        ("Recruiter / Talent Acquisition", ["recruiter", "talent acquisition", "sourcer"]),
        ("HR / People", ["hr manager", "human resources", "hr business partner", "people operations", "hr generalist", "payroll"]),
    ]),
    ("Healthcare & Life Sciences", [
        ("Clinical / Healthcare", ["clinical", "nurse", "medical", "pharmacist", "healthcare", "claims"]),
    ]),
    ("Design", [
        ("UI / UX Designer", ["ux designer", "ui designer", "ui/ux", "product designer", "graphic designer"]),
    ]),
    ("Operations & Support", [
        ("Operations", ["operations manager", "operations analyst", "supply chain", "logistics", "procurement"]),
        ("Customer / Technical Support", ["customer support", "technical support", "support engineer", "help desk", "service desk"]),
    ]),
]


def _rx(phrase: str):
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?:s|es)?(?![a-z0-9])")


ROLES: Dict[str, List] = {label: [_rx(p) for p in phrases] for _cat, roles in CATALOG for label, phrases in roles}
CATEGORY_OF = {label: cat for cat, roles in CATALOG for label, _p in roles}


def roles_of(title: str) -> List[str]:
    t = (title or "").lower()
    return [label for label, rxs in ROLES.items() if any(r.search(t) for r in rxs)]


def matches(title: str, picked: Iterable[str]) -> bool:
    t = (title or "").lower()
    return any(r.search(t) for label in picked for r in ROLES.get(label, []))


def counts(jobs: Iterable[Dict]) -> Dict:
    """{'categories': [{name, count, roles: [{label, count}]}]} for the given jobs (a job counts once per category)."""
    role_n = {label: 0 for label in ROLES}
    cat_n = {cat: 0 for cat, _ in CATALOG}
    for j in jobs:
        found = roles_of(j.get("title") or "")
        for label in found:
            role_n[label] += 1
        for cat in {CATEGORY_OF[label] for label in found}:
            cat_n[cat] += 1
    return {"categories": [{"name": cat, "count": cat_n[cat], "roles": [{"label": label, "count": role_n[label]} for label, _p in roles]}
                           for cat, roles in CATALOG]}


def suggest(title: str) -> List[str]:
    """Roles for a consultant's title (the picker's starting choice on their page)."""
    return roles_of(title)[:3]
