#!/usr/bin/env python3
"""Generate a static Hebrew flight-deals site from the live lifecycle database."""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]
SITE_DIR = Path(__file__).resolve().parent
DB_PATH = PROJECT / "deals.db"
sys.path.insert(0, str(PROJECT))

from destinations import DESTINATIONS  # noqa: E402
from telegram_publisher import build_urls  # noqa: E402

ISRAEL = ZoneInfo("Asia/Jerusalem")
HE_WEEKDAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed


def load_destinations(db_path: Path = DB_PATH) -> tuple[list[dict], datetime]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT deal_key, price_ils, updated_at, deal_json FROM posts "
            "WHERE status='active' ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, dict] = {}
    latest = datetime.min.replace(tzinfo=ZoneInfo("UTC"))
    for row in rows:
        latest = max(latest, parse_datetime(row["updated_at"]))
        payload = json.loads(row["deal_json"])
        deals = payload.get("deals") or [payload.get("deal", payload)]
        for deal in deals:
            if not deal:
                continue
            destination = str(deal["destination"]).upper()
            info = DESTINATIONS.get(destination)
            if not info:
                continue
            bucket = grouped.setdefault(destination, {
                "code": destination,
                "city": info["city_he"],
                "country": info["country_he"],
                "city_en": info["search_en"],
                "country_slug": info["country_slug"],
                "image": info["image"],
                "description": info.get("description_he", ""),
                "deals": {},
            })
            dates = (deal["depart_date"], deal["return_date"])
            previous = bucket["deals"].get(dates)
            if previous is None or int(deal["price_ils"]) < int(previous["price_ils"]):
                bucket["deals"][dates] = deal

    destinations = []
    for bucket in grouped.values():
        bucket["deals"] = sorted(
            bucket["deals"].values(),
            key=lambda deal: (deal["depart_date"], deal["return_date"], int(deal["price_ils"])),
        )
        bucket["min_price"] = min(int(deal["price_ils"]) for deal in bucket["deals"])
        destinations.append(bucket)
    destinations.sort(key=lambda item: (item["min_price"], item["city"]))
    return destinations, latest


def format_date(value: str) -> tuple[str, str]:
    parsed = datetime.strptime(value, "%Y-%m-%d")
    return parsed.strftime("%d/%m"), HE_WEEKDAYS[parsed.weekday()]


def deal_row(deal: dict, destination: dict) -> str:
    depart, depart_day = format_date(deal["depart_date"])
    returned, return_day = format_date(deal["return_date"])
    nights = (datetime.strptime(deal["return_date"], "%Y-%m-%d") - datetime.strptime(deal["depart_date"], "%Y-%m-%d")).days
    price = int(deal["price_ils"])
    deal_id = f"{destination['code']}-{deal['depart_date']}-{deal['return_date']}"
    ticket_link = str(deal.get("ticket_link") or "").strip()
    if not ticket_link:
        raise ValueError("ticket_link is required for a verified website deal")
    urls = build_urls(
        destination["code"],
        destination["city"],
        destination["city_en"],
        destination["country_slug"],
        datetime.strptime(deal["depart_date"], "%Y-%m-%d").date(),
        datetime.strptime(deal["return_date"], "%Y-%m-%d").date(),
        ticket_link=ticket_link,
    )
    return f"""
      <tr class="deal-row" data-price="{price}" data-deal-id="{esc(deal_id)}">
        <td class="dates"><bdi>{esc(depart)}–{esc(returned)}</bdi><small>{esc(depart_day)} עד {esc(return_day)}</small></td>
        <td>{nights}</td>
        <td><bdi>TLV ⇄ {esc(deal['destination'])}</bdi></td>
        <td class="price">{price} ₪</td>
        <td><a class="row-cta" href="{esc(urls['flight_url'])}" target="_blank" rel="nofollow sponsored noopener" aria-label="בדיקת טיסה לתאריכים {esc(depart)} עד {esc(returned)}">טיסה</a></td>
      </tr>
      <tr class="services-row" data-price="{price}" data-deal-id="{esc(deal_id)}"><td colspan="5"><nav class="service-links" aria-label="שירותים משלימים לתאריכים {esc(depart)} עד {esc(returned)}">
        <a href="{esc(urls['hotel_url'])}" target="_blank" rel="nofollow sponsored noopener">🏨 מלון</a>
        <a href="{esc(urls['attractions_url'])}" target="_blank" rel="nofollow sponsored noopener">🎟️ אטרקציות</a>
        <a href="{esc(urls['esim_url'])}" target="_blank" rel="nofollow sponsored noopener">📱 eSIM</a>
        <a href="{esc(urls['tours_url'])}" target="_blank" rel="nofollow noopener">🗣️ סיורים בעברית</a>
        <a href="{esc(urls['car_url'])}" target="_blank" rel="nofollow sponsored noopener">🚗 רכב</a>
      </nav><small class="affiliate-note">קישורי שותפים — ייתכן שנקבל עמלה. המחיר והזמינות נקבעים באתר הספק.</small></td></tr>"""


def destination_card(destination: dict) -> str:
    rows = "".join(deal_row(deal, destination) for deal in destination["deals"])
    search_blob = esc(f"{destination['city']} {destination['country']} {destination['code']}").lower()
    return f"""
    <article class="deal-card" data-search="{search_blob}" data-price="{destination['min_price']}">
      <div class="card-photo">
        <img src="{esc(destination['image'])}" alt="{esc(destination['city'])}, {esc(destination['country'])}" loading="lazy">
        <div class="photo-shade"></div>
        <div class="destination-title">
          <span>{esc(destination['country'])}</span>
          <h2>{esc(destination['city'])}</h2>
        </div>
        <div class="from-price"><small>החל מ־</small><strong>{destination['min_price']} ₪</strong></div>
      </div>
      <div class="card-body">
        <p>{esc(destination['description'])}</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>תאריכים</th><th>לילות</th><th>מסלול</th><th>מחיר</th><th></th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>
    </article>"""


BASE_STYLE = """
:root{--ink:#222;--muted:#555;--line:#dedede;--accent:#b0003a;--accent-dark:#85002b;--soft:#f7f7f7;--max:1240px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#fff;color:var(--ink);font-family:"Arial Hebrew",Arial,"Segoe UI",sans-serif;line-height:1.5}a{color:inherit}.site-header{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.96);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}.nav{max-width:var(--max);margin:auto;min-height:74px;padding:8px 24px;display:flex;align-items:center;gap:16px}.brand{display:flex;align-items:center;gap:12px;text-decoration:none;font-weight:800}.brand img{width:54px;height:54px;border-radius:50%;object-fit:cover;border:2px solid #fff;box-shadow:0 1px 8px rgba(0,0,0,.16)}.brand span{font-size:18px}.nav-links{margin-right:auto;display:flex;gap:18px;color:var(--muted);font-size:14px}.nav-links a{text-decoration:none}.telegram-btn,.primary-btn{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 18px;border-radius:12px;background:var(--accent);color:white;text-decoration:none;font-weight:800;border:0}.telegram-btn:hover,.primary-btn:hover{background:var(--accent-dark)}main{max-width:var(--max);margin:auto;padding:0 24px 60px}.intro{display:grid;grid-template-columns:1.2fr .8fr;gap:34px;align-items:end;padding:34px 0 18px}.eyebrow{color:var(--accent);font-weight:900;font-size:14px}.intro h1{font-size:clamp(34px,4.2vw,54px);line-height:1.06;letter-spacing:-1.4px;margin:7px 0 12px;max-width:720px;text-wrap:balance}.intro p{font-size:17px;color:var(--muted);max-width:710px;margin:0}.live-panel{background:var(--soft);border:1px solid var(--line);border-radius:22px;padding:20px 22px}.live-panel strong{font-size:34px;display:block;line-height:1}.live-panel span{color:var(--muted)}.search-shell{position:sticky;top:84px;z-index:15;background:white;padding:8px 0 16px}.searchbar{display:grid;grid-template-columns:1fr 180px 130px;gap:10px;padding:10px;border:1px solid #ddd;border-radius:18px;background:white;box-shadow:0 2px 6px rgba(0,0,0,.04),0 8px 24px rgba(0,0,0,.08)}.searchbar input,.searchbar select{width:100%;min-height:48px;border:0;background:var(--soft);border-radius:11px;padding:0 15px;font:inherit;color:var(--ink);outline:none}.searchbar input:focus,.searchbar select:focus{box-shadow:0 0 0 2px var(--accent)}.result-count{display:flex;align-items:center;justify-content:center;color:var(--muted);font-weight:700}.deal-grid{display:grid;gap:28px}.deal-card{border:1px solid var(--line);border-radius:24px;overflow:hidden;background:white;box-shadow:0 0 0 1px rgba(0,0,0,.01),0 2px 8px rgba(0,0,0,.04),0 10px 28px rgba(0,0,0,.06)}.card-photo{height:290px;position:relative;overflow:hidden}.card-photo img{width:100%;height:100%;object-fit:cover;display:block}.photo-shade{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.04),rgba(0,0,0,.66))}.destination-title{position:absolute;right:26px;bottom:24px;color:white;background:#111;padding:8px 12px;border-radius:10px}.destination-title span{font-size:15px;font-weight:700}.destination-title h2{font-size:40px;line-height:1;margin:2px 0}.from-price{position:absolute;left:22px;bottom:22px;background:white;border-radius:15px;padding:10px 15px;box-shadow:0 4px 15px rgba(0,0,0,.15)}.from-price small{display:block;color:var(--muted);font-size:11px}.from-price strong{font-size:24px;color:var(--accent)}.card-body{padding:22px 26px 26px}.card-body>p{color:var(--muted);margin:0 0 18px}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:720px;text-align:right}th{font-size:12px;color:var(--muted);padding:10px 12px;border-bottom:1px solid var(--line)}td{padding:13px 12px;border-bottom:1px solid #eee;font-size:14px}tbody tr:last-child td{border-bottom:0}.dates small{display:block;color:var(--muted);font-size:11px}.price{font-size:17px;font-weight:900;white-space:nowrap}.row-cta{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:0 14px;border-radius:10px;background:var(--ink);color:white;text-decoration:none;font-weight:800;white-space:nowrap}.row-cta:hover{background:var(--accent)}.empty{display:none;text-align:center;padding:60px 20px;color:var(--muted)}.trust{margin:50px 0 10px;padding:28px;border-radius:24px;background:var(--soft);display:grid;grid-template-columns:repeat(3,1fr);gap:24px}.trust h3{margin:0 0 6px}.trust p{margin:0;color:var(--muted);font-size:14px}.site-footer{border-top:1px solid var(--line);background:#fafafa}.footer-inner{max-width:var(--max);margin:auto;padding:34px 24px;display:flex;align-items:center;gap:20px;color:var(--muted);font-size:13px}.footer-inner nav{margin-right:auto;display:flex;gap:16px;flex-wrap:wrap}.footer-inner a{text-decoration:none}.legal-page{max-width:820px;margin:50px auto 80px;padding:0 24px}.legal-page h1{font-size:42px}.legal-page h2{margin-top:32px}.legal-page p,.legal-page li{color:#4d4d4d}.notice{background:#fff7f8;border:1px solid #ffd4dc;border-radius:16px;padding:18px}.hide{display:none!important}
@media(max-width:780px){.nav{padding:8px 14px}.brand span{font-size:14px}.nav-links{display:none}.telegram-btn{padding:0 12px;font-size:13px}.intro{grid-template-columns:1fr;gap:18px;padding-top:26px}.intro h1{letter-spacing:-1px}.search-shell{top:75px}.searchbar{grid-template-columns:1fr 120px}.result-count{grid-column:1/-1;min-height:28px}.card-photo{height:230px}.destination-title h2{font-size:32px}.card-body{padding:18px 16px}.trust{grid-template-columns:1fr}.footer-inner{align-items:flex-start;flex-direction:column}.footer-inner nav{margin-right:0}}
@media(prefers-reduced-motion:no-preference){.deal-card{transition:transform .2s,box-shadow .2s}.deal-card:hover{transform:translateY(-2px);box-shadow:0 0 0 1px rgba(0,0,0,.02),0 4px 12px rgba(0,0,0,.06),0 16px 38px rgba(0,0,0,.1)}}
"""

BASE_STYLE += """
.skip-link{position:fixed;right:16px;top:-80px;z-index:100;background:#111;color:#fff;padding:12px 18px;border-radius:0 0 10px 10px;font-weight:800;text-decoration:none}.skip-link:focus{top:0}
:focus-visible{outline:3px solid #005fcc;outline-offset:3px}.service-links{display:flex;gap:8px;flex-wrap:wrap;padding:3px 0}.service-links a{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:7px 12px;border:1px solid #aaa;border-radius:10px;background:#fff;text-decoration:none;font-weight:700}.service-links a:hover{border-color:var(--accent);color:var(--accent-dark)}.services-row td{padding-top:5px;background:#fcfcfc}.affiliate-note{display:block;margin-top:7px;color:var(--muted)}.sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.legal-page a{text-decoration-thickness:1px;text-underline-offset:3px}.legal-page .source-list{font-size:14px}.legal-page .contact-box{background:var(--soft);border:1px solid var(--line);border-radius:16px;padding:18px}
@media(max-width:780px){.service-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:calc(100vw - 74px);max-width:100%}.service-links a{padding:7px 8px}.services-row td{white-space:normal}.affiliate-note{display:block;width:calc(100vw - 74px);max-width:100%}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto!important}*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
"""


def shell(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="דילים מאומתים לטיסות הלוך־חזור מתל אביב עד 499 ₪"><title>{esc(title)}</title><style>{BASE_STYLE}</style></head><body><a class="skip-link" href="#main-content">דילוג לתוכן הראשי</a><header class="site-header"><div class="nav"><a class="brand" href="index.html"><img src="assets/channel-logo.jpg" alt="לוגו לטוס הלוך־חזור עד 499 ₪"><span>לטוס הלוך־חזור עד 499 ₪</span></a><nav class="nav-links" aria-label="ניווט ראשי"><a href="about.html">אודות</a><a href="affiliate-disclosure.html">גילוי נאות</a><a href="privacy.html">פרטיות</a><a href="accessibility.html">נגישות</a></nav><a class="telegram-btn" href="https://t.me/flights_under_499" target="_blank" rel="noopener">לערוץ בטלגרם</a></div></header>{body}<footer class="site-footer"><div class="footer-inner"><span>© 2026 לטוס הלוך־חזור עד 499 ₪</span><nav aria-label="ניווט תחתון"><a href="about.html">אודות ויצירת קשר</a><a href="affiliate-disclosure.html">גילוי נאות</a><a href="privacy.html">פרטיות</a><a href="terms.html">תנאי שימוש</a><a href="accessibility.html">הצהרת נגישות</a></nav></div></footer></body></html>"""


def generate(db_path: Path = DB_PATH, output_dir: Path = SITE_DIR) -> dict:
    destinations, latest = load_destinations(db_path)
    total_dates = sum(len(item["deals"]) for item in destinations)
    latest_local = latest.astimezone(ISRAEL).strftime("%d/%m/%Y בשעה %H:%M") if destinations else "—"
    cards = "".join(destination_card(item) for item in destinations)
    index_body = f"""
<main id="main-content"><section class="intro"><div><div class="eyebrow">דילים שנבדקו במערכת בזמן אמת</div><h1>פחות חיפושים.<br>יותר תאריכים שמתאימים לך.</h1><p>כל הדילים הפעילים עד 499 ₪ מרוכזים לפי יעד. המחירים נבדקים לפני הפרסום ועשויים להשתנות באתר ההזמנה.</p></div><aside class="live-panel" aria-label="מצב הדילים"><strong>{len(destinations)}</strong><span>יעדים פעילים · {total_dates} אפשרויות תאריך</span><hr style="border:0;border-top:1px solid #ddd;margin:18px 0"><small>עדכון אחרון: {esc(latest_local)}</small></aside></section>
<section class="search-shell" aria-label="סינון דילים"><div class="searchbar"><label class="sr-only" for="search">חיפוש לפי יעד, מדינה או קוד שדה</label><input id="search" type="search" placeholder="חיפוש לפי יעד, מדינה או קוד שדה…"><label class="sr-only" for="price">מחיר מרבי</label><select id="price"><option value="499">עד 499 ₪</option><option value="450">עד 450 ₪</option><option value="400">עד 400 ₪</option><option value="350">עד 350 ₪</option><option value="300">עד 300 ₪</option></select><div class="result-count" aria-live="polite"><span id="count">{len(destinations)}</span>&nbsp;יעדים</div></div></section>
<section id="deals" class="deal-grid">{cards}</section><div id="empty" class="empty"><h2>לא נמצאו יעדים מתאימים</h2><p>אפשר לשנות את החיפוש או את המחיר המרבי.</p></div>
<section class="trust"><div><h3>מחיר מאומת</h3><p>המערכת בודקת את המחיר המוצג בקישור לפני פרסום הדיל.</p></div><div><h3>ישירות מתל אביב</h3><p>הדילים באתר הם לטיסות ישירות הלוך־חזור, בהתאם למדיניות הערוץ.</p></div><div><h3>ללא הבטחת מלאי</h3><p>מחירי טיסות משתנים במהירות. המחיר הקובע הוא המחיר שיופיע באתר ההזמנה.</p></div></section></main>
<script>(()=>{{const q=document.getElementById('search'),p=document.getElementById('price'),cards=[...document.querySelectorAll('.deal-card')],count=document.getElementById('count'),empty=document.getElementById('empty');function filter(){{const term=q.value.trim().toLowerCase(),max=Number(p.value);let visible=0;cards.forEach(card=>{{const searchMatch=card.dataset.search.includes(term),rows=[...card.querySelectorAll('.deal-row')];let visibleRows=0;rows.forEach(row=>{{const show=searchMatch&&Number(row.dataset.price)<=max,id=row.dataset.dealId;row.classList.toggle('hide',!show);const services=card.querySelector(`.services-row[data-deal-id="${{id}}"]`);if(services)services.classList.toggle('hide',!show);if(show)visibleRows++}});card.classList.toggle('hide',visibleRows===0);if(visibleRows)visible++}});count.textContent=visible;empty.style.display=visible?'none':'block'}}q.addEventListener('input',filter);p.addEventListener('change',filter)}})();</script>"""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(shell("דילים לטיסות עד 499 ₪", index_body), encoding="utf-8")

    pages = {
        "about.html": ("אודות", """<main id="main-content" class="legal-page"><h1>אודות ויצירת קשר</h1><p>האתר מרכז דילים פעילים לטיסות הלוך־חזור מתל אביב עד 499 ₪ ומפנה לשירותי תיירות משלימים.</p><h2>איך זה עובד?</h2><p>מערכת אוטומטית מאתרת מועמדים, מסננת טיסות ישירות ובודקת את המחיר בקישור ההזמנה לפני הפרסום.</p><div class="notice"><b>חשוב:</b> האתר אינו סוכנות נסיעות ואינו מוכר כרטיסים. ההזמנה, התשלום והשירות מתבצעים מול הספק החיצוני שאליו מוביל הקישור.</div><h2>יצירת קשר</h2><div class="contact-box"><p>האתר נמצא בתצוגה מקומית. לפני העלאה לציבור יפורסמו כאן כתובת דוא״ל פעילה ופרטי המפעיל. בשלב זה ניתן לצפות בערוץ הציבורי:</p><p><a class="primary-btn" href="https://t.me/flights_under_499">לטוס הלוך־חזור עד 499 ₪</a></p></div></main>"""),
        "affiliate-disclosure.html": ("גילוי נאות", """<main id="main-content" class="legal-page"><h1>גילוי נאות — קישורי שותפים</h1><p>חלק מהקישורים באתר הם קישורי שותפים. אם תבצעו הזמנה דרך קישור כזה, ייתכן שנקבל עמלה מהספק, ללא תוספת מחיר עבורכם.</p><p>העמלה אינה מחליפה בדיקה עצמאית של המחיר, הזמינות והתנאים אצל הספק ואינה קובעת אם דיל טיסה יפורסם.</p><p>האתר מציג מידע והפניות בלבד. ההזמנה, החיוב, אישור ההזמנה, הכבודה, השינויים, הביטול ושירות הלקוחות נעשים מול הספק החיצוני. חלק מהקישורים, ובהם קישור לסיורים בעברית, עשויים להיות קישורים ישירים שאינם מזכים בעמלה.</p><p>ליד קישורי השותפים בדף הדילים מופיע גילוי מקוצר.</p></main>"""),
        "privacy.html": ("מדיניות פרטיות", """<main id="main-content" class="legal-page"><h1>מדיניות פרטיות</h1><p>בגרסת האתר הנוכחית אין הרשמה, טפסים או תשלום, ואיננו מבקשים שם, כתובת דוא״ל או פרטי תשלום.</p><p>איננו מפעילים כלי ניתוח או פיקסלי פרסום. עם זאת, תמונות היעדים נטענות משרתי <b>Unsplash</b> ו־<b>Wikimedia Commons</b>. בעת טעינת דף הדפדפן פונה אליהם אוטומטית; ספקים אלה עשויים לקבל כתובת IP, כתובת הדף המפנה ופרטים טכניים על הדפדפן, ועשויים להציב עוגיות בהתאם למדיניות שלהם. אין לנו שליטה על עיבוד זה.</p><p>ספק האחסון העתידי עשוי לשמור לוגים טכניים מוגבלים, כגון כתובת IP, לצורכי אבטחה ותפעול בהתאם למדיניותו; זהות הספק ופרטי השמירה יעודכנו כאן לפני העלייה לאוויר.</p><p>לחיצה על קישור מעבירה לאתר חיצוני, הכפוף למדיניות הפרטיות ולתנאים שלו. קישורי שותפים כוללים מזהי הפניה למדידת עסקאות, והספק החיצוני עשוי להשתמש בטכנולוגיות מעקב משלו.</p><p>אם יתווספו טופס, דיוור, כלי מדידה או עוגיות נוספות, המדיניות ומנגנון ההסכמה יעודכנו לפני הפעלתם.</p><p>האתר נמצא בתצוגה מקומית. כתובת דוא״ל לפניות פרטיות ופרטי המפעיל יפורסמו לפני העלייה לציבור.</p><p><small>עדכון אחרון: 24/10/2026.</small></p></main>"""),
        "terms.html": ("תנאי שימוש", """<main id="main-content" class="legal-page"><h1>תנאי שימוש</h1><p>המידע באתר מיועד לאיתור דילים והפניה לשירותים חיצוניים ואינו הצעה מחייבת. מחירים, זמינות, שעות ותנאים עשויים להשתנות עד להשלמת ההזמנה.</p><p>איננו צד לעסקה בין המשתמש לספק החיצוני ואיננו מקבלים באתר הזמנות או תשלומים. המחיר והתנאים הקובעים הם אלה שיוצגו באתר הספק לפני התשלום.</p><p>באחריות המשתמש לבדוק את זהות הספק, דרישות הכניסה והדרכון, ביטוח, כבודה, מיסים, תנאי שינוי וביטול ומדיניות הפרטיות של הספק. תנאי הכבודה בהתאם לסוג הכרטיס ולתנאי הספק.</p><p>אין בתנאים אלה כדי לגרוע מזכות שאי־אפשר להתנות עליה לפי דין.</p><p><small>עדכון אחרון: 24/10/2026.</small></p></main>"""),
        "accessibility.html": ("הצהרת נגישות", """<main id="main-content" class="legal-page"><h1>הצהרת נגישות</h1><p>אנו פועלים להנגיש את אתר „לטוס הלוך־חזור עד 499 ₪” בהתאם לתקנות שוויון זכויות לאנשים עם מוגבלות (התאמות נגישות לשירות), התשע״ג–2013, ולתקן הישראלי ת״י 5568 ברמה AA, ככל שהחובה חלה ובהתאם לדין.</p><h2>התאמות שבוצעו</h2><ul><li>שפה וכיוון קריאה מוגדרים בעברית וב־RTL.</li><li>מבנה כותרות ואזורים סמנטיים, קישור דילוג לתוכן ושמות נגישים לקישורים.</li><li>ניווט במקלדת ומוקד מקלדת בולט.</li><li>טקסט חלופי לתמונות יעד ותוכן המחירים והתאריכים כטקסט.</li><li>תמיכה בהפחתת תנועה, הגדלת תצוגה והתאמה למסכים ניידים.</li><li>הודעות שינוי של מסנן הדילים מוגדרות לקורא מסך.</li></ul><h2>בדיקות ומגבלות</h2><p>האתר נבדק בבדיקות אוטומטיות ובבדיקת מקלדת בסיסית. קישורים מובילים לאתרי ספקים חיצוניים, והנגישות בהם נמצאת באחריות מפעיליהם. אם נמצא קושי באתר שלנו, נספק ככל האפשר את המידע בחלופה נגישה.</p><h2>דיווח על בעיית נגישות</h2><div class="contact-box"><p>האתר נמצא בתצוגה מקומית. לפני העלאה לציבור יפורסמו כאן כתובת דוא״ל פעילה ופרטי איש הקשר לנגישות. אין להסתמך על קישור הערוץ הציבורי כערוץ פנייה פרטי.</p></div><p>תאריך בדיקה ועדכון אחרון: 24/10/2026.</p><h2>מקור רשמי</h2><p class="source-list"><a href="https://www.gov.il/he/pages/website_accessibility">נציבות שוויון זכויות לאנשים עם מוגבלות — נגישות אתרי אינטרנט</a></p></main>"""),
    }
    for filename, (title, body) in pages.items():
        (output_dir / filename).write_text(shell(f"{title} | טיסות עד 499 ₪", body), encoding="utf-8")
    (output_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    manifest = {"destinations": len(destinations), "date_options": total_dates, "updated_at": latest.isoformat(), "files": ["index.html", *pages.keys(), "robots.txt"]}
    (output_dir / "site-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path, default=SITE_DIR)
    args = parser.parse_args()
    print(json.dumps(generate(args.db, args.output), ensure_ascii=False, indent=2))
