import os
from datetime import date, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import requests

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:5000")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
MAIL_FROM = os.getenv("MAIL_FROM")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL")
DAYS_BEFORE = int(os.getenv("DAYS_BEFORE", "60"))
TZ = os.getenv("TZ", "UTC")


def _parse_iso(d: str) -> date:
    y, m, dd = d.split("-")
    return date(int(y), int(m), int(dd))


def send_email(subject: str, html: str):
    if not SENDGRID_API_KEY:
        raise RuntimeError("Missing SENDGRID_API_KEY")
    if not MAIL_FROM:
        raise RuntimeError("Missing MAIL_FROM")
    if not NOTIFY_EMAIL:
        raise RuntimeError("Missing NOTIFY_EMAIL")

    message = Mail(
        from_email=MAIL_FROM,
        to_emails=NOTIFY_EMAIL,
        subject=subject,
        html_content=html,
    )
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    sg.send(message)


def check_expirations():
    target = date.today() + timedelta(days=DAYS_BEFORE)

    try:
        r = requests.get(f"{BACKEND_URL}/contracts", timeout=30)
        r.raise_for_status()
        contracts = r.json()
    except Exception as e:
        print("ERROR fetching contracts:", str(e))
        return

    matches = []
    for c in contracts:
        end_date = c.get("endDate")
        if not end_date:
            continue
        try:
            if _parse_iso(end_date) == target:
                matches.append(c)
        except Exception:
            continue

    if not matches:
        print(f"[OK] No expirations for {target.isoformat()}")
        return

    rows = ""
    for c in matches:
        rows += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #eee">{c.get("id","")}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">{c.get("propertyLabel","")}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">{c.get("ownerName","")}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">{c.get("tenantName","")}</td>
          <td style="padding:8px;border-bottom:1px solid #eee">{c.get("endDate","")}</td>
        </tr>
        """

    subject = f"[PropTech Contracts] Vencimientos en {DAYS_BEFORE} días ({target.isoformat()})"
    html = f"""
    <div style="font-family: Arial, sans-serif">
      <h2>Contratos por vencer</h2>
      <p>Estos contratos vencen el <b>{target.isoformat()}</b> (en {DAYS_BEFORE} días).</p>

      <table style="border-collapse:collapse;width:100%;max-width:900px">
        <thead>
          <tr>
            <th align="left" style="padding:8px;border-bottom:2px solid #ddd">ID</th>
            <th align="left" style="padding:8px;border-bottom:2px solid #ddd">Propiedad</th>
            <th align="left" style="padding:8px;border-bottom:2px solid #ddd">Propietario</th>
            <th align="left" style="padding:8px;border-bottom:2px solid #ddd">Inquilino</th>
            <th align="left" style="padding:8px;border-bottom:2px solid #ddd">Vence</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>
    """

    try:
        send_email(subject, html)
        print(f"[OK] Sent expiration email for {len(matches)} contract(s)")
    except Exception as e:
        print("ERROR sending email:", str(e))


def main():
    sched = BlockingScheduler(timezone=TZ)

    # 09:00 todos los días (hora de TZ)
    sched.add_job(check_expirations, "cron", hour=9, minute=0)

    print(f"Notifier worker started. TZ={TZ}. Schedule: daily 09:00")
    # Ejecuta una vez al iniciar (útil en Render para ver logs)
    check_expirations()

    sched.start()


if __name__ == "__main__":
    main()