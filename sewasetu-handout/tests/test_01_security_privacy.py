import pytest
import requests

BASE_URL = "http://localhost:8000"

def test_citizen_cannot_view_other_citizen_application():
    """
    SECURITY TEST (BOLA / IDOR):
    Citizen A must not be allowed to view Citizen B's application.
    """
    session_a = requests.Session()
    # Log in as Citizen 1 (mobile: 9861908220, DOB: 20/05/1961 -> 20051961)
    login_resp = session_a.post(
        f"{BASE_URL}/status",
        data={"mobile": "9861908220", "password": "20051961"},
        allow_redirects=False,
    )
    assert login_resp.status_code == 302, "Citizen 1 login failed"

    # Attempt to access Citizen 2's application (ID: 2, belonging to mobile 6950273123)
    resp = session_a.get(f"{BASE_URL}/application/2", allow_redirects=False)
    # The application should deny access (403 Forbidden or redirect to own/status with error)
    # Under vulnerable code, this returns 200 OK and exposes Citizen 2's personal data.
    assert resp.status_code == 403 or (resp.status_code == 302 and "/status" in resp.headers.get("Location", "")), (
        f"Expected 403 Forbidden or redirect for IDOR attempt, got {resp.status_code}"
    )
    if resp.status_code == 200:
        assert "Jatin Hussain" not in resp.text, "Citizen B's name exposed to Citizen A"


def test_unauthenticated_cannot_download_ack_pdf():
    """
    SECURITY TEST:
    Unauthenticated users must not be able to download citizen acknowledgment PDFs.
    """
    anon_session = requests.Session()
    resp = anon_session.get(f"{BASE_URL}/ack/1.pdf", allow_redirects=False)
    # Vulnerable code returns 200 OK with the PDF content
    assert resp.status_code in (401, 403, 302), (
        f"Unauthenticated user should not download ack PDF, got {resp.status_code}"
    )


def test_citizen_cannot_download_other_citizen_ack_pdf():
    """
    SECURITY TEST:
    Citizen A must not be allowed to download Citizen B's acknowledgment PDF.
    """
    session_a = requests.Session()
    session_a.post(
        f"{BASE_URL}/status",
        data={"mobile": "9861908220", "password": "20051961"},
        allow_redirects=False,
    )
    # Citizen 1 attempts to download Citizen 2's PDF (ID: 2)
    resp = session_a.get(f"{BASE_URL}/ack/2.pdf", allow_redirects=False)
    assert resp.status_code in (403, 404), (
        f"Citizen A must not download Citizen B's ack PDF, got {resp.status_code}"
    )


def test_citizen_cannot_approve_or_reject_application():
    """
    SECURITY TEST (Privilege Escalation):
    A logged-in citizen without admin role must not be able to approve or reject applications.
    """
    session_a = requests.Session()
    session_a.post(
        f"{BASE_URL}/status",
        data={"mobile": "9861908220", "password": "20051961"},
        allow_redirects=False,
    )
    # Citizen attempts to approve application 2
    resp_approve = session_a.post(f"{BASE_URL}/admin/approve/2", allow_redirects=False)
    # Vulnerable code checks session.get('logged_in') and returns 302 (success)
    assert resp_approve.status_code == 403, (
        f"Non-admin citizen should receive 403 on /admin/approve, got {resp_approve.status_code}"
    )

    # Citizen attempts to reject application 2
    resp_reject = session_a.post(f"{BASE_URL}/admin/reject/2", allow_redirects=False)
    assert resp_reject.status_code == 403, (
        f"Non-admin citizen should receive 403 on /admin/reject, got {resp_reject.status_code}"
    )


def test_admin_can_access_admin_endpoints():
    """
    Ensure legitimate departmental admin continues to work.
    """
    admin_session = requests.Session()
    login_resp = admin_session.post(
        f"{BASE_URL}/admin",
        data={"username": "admin", "password": "sewasetu@123"},
        allow_redirects=False,
    )
    assert login_resp.status_code == 302
    assert login_resp.headers.get("Location") == "/admin/dashboard"

    dash_resp = admin_session.get(f"{BASE_URL}/admin/dashboard")
    assert dash_resp.status_code == 200
    assert "Applications by Status" in dash_resp.text


def test_sql_injection_resistance_in_password_reset():
    """
    SECURITY TEST:
    Verify /status/reset resists SQL injection payloads without 500 error or syntax crashes.
    """
    anon_session = requests.Session()
    sqli_payload = "9861908220' OR '1'='1"
    resp = anon_session.post(
        f"{BASE_URL}/status/reset",
        data={"mobile": sqli_payload},
        allow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Internal Server Error" not in resp.text
