"""
email_report.py
===============
HTML email assembly and SMTP delivery.

Security contract
-----------------
- The SMTP ``From`` and ``To`` fields are BOTH set to ``GMAIL_USER_EMAIL``.
  It is structurally impossible for this module to send email to any external
  address: both fields are derived from the same variable, and there is no
  code path that accepts a recipient argument.
- Connects to ``smtp.gmail.com:465`` via ``smtplib.SMTP_SSL`` (enforced TLS).
- Authenticates using the App Password — never the account password.
"""

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import GMAIL_APP_PASSWORD, GMAIL_USER_EMAIL, SMTP_HOST, SMTP_PORT, TIER_A, TIER_B
from models import JobEvaluation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier display metadata
# ---------------------------------------------------------------------------

_TIER_META: dict[str, dict] = {
    TIER_A: {
        "label": "⭐ Tier A — Strong Match",
        "color": "#1a7f37",          # deep green
        "bg": "#dafbe1",
        "badge_bg": "#1a7f37",
        "badge_color": "#ffffff",
    },
    TIER_B: {
        "label": "🔶 Tier B — Fuzzy Match",
        "color": "#9a6700",          # amber
        "bg": "#fff8c5",
        "badge_bg": "#d4a017",
        "badge_color": "#1c1c1c",
    },
}


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def build_html(
    jobs: list[JobEvaluation],
    youtube_links: list[dict] | None = None,
) -> str:
    """
    Render *jobs* as a styled HTML email body grouped by tier.

    Parameters
    ----------
    jobs : list[JobEvaluation]
        AI-evaluated jobs to display in tier sections.
    youtube_links : list[dict] | None
        Optional YouTube-sourced link dicts (bypass AI).  Each dict must
        have ``"title"`` and ``"url"`` keys.

    Returns
    -------
    str
        Complete HTML document string.
    """
    run_date = datetime.now(timezone.utc).strftime("%A, %d %B %Y — %H:%M UTC")

    # Group jobs
    tier_a_jobs = [j for j in jobs if j.match_tier == TIER_A]
    tier_b_jobs = [j for j in jobs if j.match_tier == TIER_B]

    def job_card(job: JobEvaluation) -> str:
        meta = _TIER_META.get(job.match_tier, _TIER_META[TIER_B])
        return f"""
        <div style="
            background: #ffffff;
            border: 1px solid #e1e4e8;
            border-left: 4px solid {meta['color']};
            border-radius: 6px;
            margin: 12px 0;
            padding: 16px 20px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        ">
            <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
                <h3 style="margin:0; font-size:16px; color:#24292f;">{_esc(job.job_title)}</h3>
                <span style="
                    background:{meta['badge_bg']};
                    color:{meta['badge_color']};
                    font-size:11px;
                    font-weight:600;
                    padding:3px 10px;
                    border-radius:20px;
                    white-space:nowrap;
                ">Score {job.match_score}/100</span>
            </div>
            <p style="margin:6px 0 0; color:#57606a; font-size:14px;">
                🏢 <strong>{_esc(job.company)}</strong>
            </p>
            <p style="margin:8px 0; color:#444d56; font-size:13px; line-height:1.5;">
                {_esc(job.logic_summary)}
            </p>
            <a href="{_esc(job.clean_apply_url)}"
               style="
                   display:inline-block;
                   margin-top:8px;
                   padding:8px 18px;
                   background:{meta['color']};
                   color:#ffffff;
                   text-decoration:none;
                   border-radius:5px;
                   font-size:13px;
                   font-weight:600;
               ">Apply Here →</a>
        </div>
        """

    def section(tier_jobs: list[JobEvaluation], tier_key: str) -> str:
        if not tier_jobs:
            return ""
        meta = _TIER_META[tier_key]
        cards = "\n".join(job_card(j) for j in tier_jobs)
        return f"""
        <div style="margin-bottom:32px;">
            <h2 style="
                font-size:18px;
                color:{meta['color']};
                border-bottom:2px solid {meta['color']};
                padding-bottom:6px;
                margin-bottom:4px;
            ">{meta['label']} ({len(tier_jobs)} role{"s" if len(tier_jobs) != 1 else ""})</h2>
            {cards}
        </div>
        """

    def youtube_section(links: list[dict]) -> str:
        """Render the 📺 Online Study For You Links section."""
        if not links:
            return ""
        items = "\n".join(
            f"""
            <li style="margin-bottom:10px; font-size:14px; line-height:1.6;">
                <a href="{_esc(link.get('url', '#'))}"
                   style="color:#7c3aed; text-decoration:none; font-weight:600;"
                >{_esc(link.get('title', 'Link'))}</a>
                <br/>
                <span style="color:#8b949e; font-size:12px;">{_esc(link.get('url', ''))}</span>
            </li>"""
            for link in links
        )
        return f"""
        <div style="margin-bottom:32px;">
            <h2 style="
                font-size:18px;
                color:#7c3aed;
                border-bottom:2px solid #7c3aed;
                padding-bottom:6px;
                margin-bottom:12px;
            ">📺 Online Study For You Links ({len(links)} link{"s" if len(links) != 1 else ""})</h2>
            <ul style="
                list-style:none;
                padding-left:0;
                margin:0;
            ">
                {items}
            </ul>
        </div>
        """

    yt_links = youtube_links or []

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Daily Job Report</title>
</head>
<body style="margin:0; padding:0; background:#f6f8fa;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f6f8fa; padding:24px 0;">
<tr><td>
<div style="
    max-width:680px;
    margin:0 auto;
    background:#ffffff;
    border:1px solid #d0d7de;
    border-radius:10px;
    overflow:hidden;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
">
    <!-- Header -->
    <div style="
        background:linear-gradient(135deg,#0d1117 0%,#161b22 100%);
        padding:28px 32px;
        color:#ffffff;
    ">
        <h1 style="margin:0; font-size:22px; font-weight:700; letter-spacing:-0.3px;">
            💼 Daily Backend Engineer Job Report
        </h1>
        <p style="margin:6px 0 0; color:#8b949e; font-size:13px;">{run_date}</p>
        <p style="margin:10px 0 0; color:#c9d1d9; font-size:14px;">
            {len(jobs)} role{"s" if len(jobs) != 1 else ""} passed the tier filter today
            ({len(tier_a_jobs)} Tier A · {len(tier_b_jobs)} Tier B){f" · {len(yt_links)} YouTube link{'s' if len(yt_links) != 1 else ''}" if yt_links else ""}
        </p>
    </div>

    <!-- Body -->
    <div style="padding:24px 32px;">
        {section(tier_a_jobs, TIER_A)}
        {section(tier_b_jobs, TIER_B)}
        {youtube_section(yt_links)}
    </div>

    <!-- Footer -->
    <div style="
        background:#f6f8fa;
        border-top:1px solid #d0d7de;
        padding:16px 32px;
        text-align:center;
        color:#8b949e;
        font-size:11px;
    ">
        This report was generated automatically and sent from {_esc(GMAIL_USER_EMAIL)}
        to {_esc(GMAIL_USER_EMAIL)}.
        No external parties received this email.
    </div>
</div>
</td></tr>
</table>
</body>
</html>"""

    return body


def _esc(text: str) -> str:
    """Minimal HTML-escape to prevent XSS if a job title contains special chars."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# SMTP delivery
# ---------------------------------------------------------------------------

def send_report(html_body: str, job_count: int) -> bool:
    """
    Send the HTML report from ``GMAIL_USER_EMAIL`` back to ``GMAIL_USER_EMAIL``.

    Security
    --------
    Both ``From`` and ``To`` headers are set to the **same** ``GMAIL_USER_EMAIL``
    constant.  There is no parameter or config key that accepts a different
    recipient — the only person who can receive this email is the script owner.

    Parameters
    ----------
    html_body : str
        The fully rendered HTML string from ``build_html()``.
    job_count : int
        Number of jobs in the report, used in the subject line.

    Returns
    -------
    bool
        ``True`` if the email was accepted by the SMTP server; ``False`` otherwise.
    """
    run_date = datetime.now(timezone.utc).strftime("%d %b %Y")
    subject = f"[Job Report] {job_count} Backend Role{'s' if job_count != 1 else ''} — {run_date}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER_EMAIL      # ← same variable
    msg["To"] = GMAIL_USER_EMAIL        # ← same variable (no external recipient)
    msg["X-Mailer"] = "JobTrackerBot/1.0"

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    logger.info(
        "Connecting to SMTP %s:%d to send report (%d jobs) …",
        SMTP_HOST, SMTP_PORT, job_count,
    )

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(GMAIL_USER_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(
                from_addr=GMAIL_USER_EMAIL,
                to_addrs=[GMAIL_USER_EMAIL],    # list of one — always self
                msg=msg.as_string(),
            )
        logger.info("✅ Report email sent successfully to %s.", GMAIL_USER_EMAIL)
        return True

    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP authentication failed: %s", exc)
    except smtplib.SMTPException as exc:
        logger.error("SMTP error during send: %s", exc)
    except Exception as exc:   # noqa: BLE001
        logger.error("Unexpected error during SMTP send: %s", exc)

    return False
