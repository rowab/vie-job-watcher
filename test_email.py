import os, smtplib
from email.mime.text import MIMEText

host = "smtp.gmail.com"; port = 587
from_addr = os.environ.get("SMTP_FROM")
to_addr   = os.environ.get("SMTP_TO")
user      = os.environ.get("SMTP_USER")
pw        = os.environ.get("SMTP_PASS")

print("FROM:", from_addr)
print("TO:", to_addr)
print("USER:", user)
print("PASS set?:", bool(pw))

msg = MIMEText("Test e-mail VIE Watcher", "plain", "utf-8")
msg["Subject"] = "Test SMTP OK ?"
msg["From"] = from_addr
msg["To"] = to_addr

with smtplib.SMTP(host, port) as s:
    s.starttls()
    s.login(user, pw)
    s.sendmail(from_addr, [to_addr], msg.as_string())

print(" Email envoyé.")
