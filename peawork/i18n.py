"""Gettext i18n support.

Provides a ``_`` Jinja2 filter and a simple English/Hindi translation
loader.  Locale is chosen in this order: ``?lang=`` query param (sets
a session/cookie), ``Accept-Language`` header, then English.
"""
from __future__ import annotations

from fastapi import Request

SUPPORTED = ("en", "hi")


def get_locale(request: Request) -> str:
    """Choose a supported locale for the given request."""
    lang = request.query_params.get("lang")
    if lang and lang in SUPPORTED:
        return lang
    cookie = request.cookies.get("pw_lang")
    if cookie and cookie in SUPPORTED:
        return cookie
    accept = request.headers.get("accept-language", "")
    for part in accept.split(","):
        code = part.strip().split(";")[0].split("-")[0].lower().strip()
        if code in SUPPORTED:
            return code
    return "en"


# English source strings (msgid -> English text) used as default.
# Hindi translations are provided in the ``hi`` dict below.
TEXTS = {
    # Documents page
    "documents.title": "Documents",
    "documents.heading": "Document register",
    "documents.subheading": "files",
    "documents.private-files": "Private employee files. Every download is written to the audit log.",
    "documents.empty": "No documents in your scope",
    "documents.empty-hint": "Upload files from an employee record. You only see documents you are allowed to access.",
    "documents.download": "Download",
    "documents.pending": "Pending",
    "documents.yes": "Yes",
    "documents.acknowledge": "Acknowledge",
    "documents.acknowledge-for-employee": "Acknowledge for employee",
    "documents.delete": "Delete",
    "documents.confirm-read": "Confirm you have read this document?",
    "documents.confirm-delete": "Delete this document permanently?",
    "documents.th.title": "Title",
    "documents.th.employee": "Employee",
    "documents.th.category": "Category",
    "documents.th.file": "File",
    "documents.th.size": "Size",
    "documents.th.expires": "Expires",
    "documents.th.acknowledged": "Acknowledged",
    "documents.th.uploaded": "Uploaded",
    "documents.header": "Documents",
    "documents.card-header": "Files",
    "documents.file-spec": "file",
    "documents.category-label": "Document categories",
    "documents.full-workbook": "Full organisation workbook",
    # Dashboard
    "dashboard.heading": "Dashboard",
    "dashboard.welcome": "Welcome to PeaWork",
    # Health
    "health.live": "Liveness probe",
    "health.ready": "Readiness probe",
    "health.metrics": "Prometheus metrics",
    # Organisation / settings
    "org.settings-title": "Organisation settings",
    "org.settings-sub": "Identity, locale and payroll defaults. Changes are recorded in the audit log.",
}

HINDI = {
    "documents.title": "दस्तावेज़",
    "documents.heading": "दस्तावेज़ रजिस्टर",
    "documents.subheading": "फ़ाइलें",
    "documents.private-files": "निजी कर्मचारी फ़ाइलें। हर डाउनलोड ऑडिट लॉग में दर्ज होता है।",
    "documents.empty": "आपके दायरे में कोई दस्तावेज़ नहीं है",
    "documents.empty-hint": "किसी कर्मचारी रिकॉर्ड से फ़ाइलें अपलोड करें। आप केवल वही दस्तावेज़ देखते हैं जिन्हें आप एक्सेस करने के लिए अधिकृत हैं।",
    "documents.download": "डाउनलोड",
    "documents.pending": "लंबित",
    "documents.yes": "हाँ",
    "documents.acknowledge": "स्वीकार करें",
    "documents.acknowledge-for-employee": "कर्मचारी की ओर से स्वीकार करें",
    "documents.delete": "हटाएं",
    "documents.confirm-read": "क्या आपने यह दस्तावेज़ पढ़ लिया है, पुष्टि करें?",
    "documents.confirm-delete": "क्या आप इस दस्तावेज़ को स्थायी रूप से हटाना चाहते हैं?",
    "documents.th.title": "शीर्षक",
    "documents.th.employee": "कर्मचारी",
    "documents.th.category": "श्रेणी",
    "documents.th.file": "फ़ाइल",
    "documents.th.size": "आकार",
    "documents.th.expires": "समाप्ति",
    "documents.th.acknowledged": "स्वीकृत",
    "documents.th.uploaded": "अपलोड किया गया",
    "documents.header": "दस्तावेज़",
    "documents.card-header": "फ़ाइलें",
    "documents.file-spec": "फ़ाइल",
    "documents.category-label": "दस्तावेज़ श्रेणियां",
    "documents.full-workbook": "पूर्ण संगठन कार्यपुस्तिका",
    # Dashboard
    "dashboard.heading": "डैशबोर्ड",
    "dashboard.welcome": "PeaWork में आपका स्वागत है",
    # Health
    "health.live": "लाइवनेस जांच",
    "health.ready": "रेडीनेस जांच",
    "health.metrics": "प्रोमेथियस मैट्रिक्स",
    # Organisation / settings
    "org.settings-title": "संगठन सेटिंग्स",
    "org.settings-sub": "पहचान, स्थान और पेरोल डिफ़ॉल्ट। परिवर्तन ऑडिट लॉग में दर्ज किए जाते हैं।",
}


def translate(key: str, locale: str = "en") -> str:
    if locale == "hi" and key in HINDI:
        return HINDI[key]
    return TEXTS.get(key, key)
