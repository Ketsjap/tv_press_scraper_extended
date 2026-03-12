from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import os
import re
from datetime import datetime
from openai import OpenAI
import time

# --- CONFIGURATIE ---
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
JSON_FILE = "press.json"

SITES = [
    { "name": "VTM", "url": "https://communicatie.vtm.be/", "base": "https://communicatie.vtm.be" },
    { "name": "Play", "url": "https://communicatie.play.tv/", "base": "https://communicatie.play.tv" },
    { "name": "VRT 1", "url": "https://communicatie.vrt1.be/", "base": "https://communicatie.vrt1.be" },
    { "name": "Canvas", "url": "https://communicatie.vrtcanvas.be/", "base": "https://communicatie.vrtcanvas.be" }
]

client = OpenAI(api_key=OPENAI_KEY)

def get_recent_links(site):
    print(f"🌍 Bezoeken: {site['name']}...")

    # --- SPECIALE LOGICA VOOR VTM (Sitemap Methode) ---
    if site['name'] == "VTM":
        print(f"   🗺️ Gebruik sitemap route om bot-detectie te omzeilen...")
        try:
            sitemap_url = f"{site['url'].rstrip('/')}/sitemap.xml"
            # Gebruik curl_cffi met Chrome impersonation
            response = requests.get(sitemap_url, impersonate="chrome", timeout=15)
            print(f"   [DEBUG] Sitemap Status Code: {response.status_code}")
            print(f"   [DEBUG] Sitemap Response (eerste 250 tekens): {response.text[:250]}")

            if response.status_code != 200:
                print(f"   ❌ Sitemap niet bereikbaar (Status {response.status_code})")
                return []

            # Gebruik regex om alle <loc> links uit de XML te halen
            all_links = re.findall(r'<loc>(.*?)</loc>', response.text)

            article_links = []
            for link in all_links:
                slug = link.replace(site['base'], "").strip("/")
                if len(slug) > 20 and not any(x in link for x in ["/login", "/search", "/media"]):
                    article_links.append(link)

            # Pak de laatste 10 en daarvan de unieke 5
            found_list = list(dict.fromkeys(article_links))[-10:]
            # Neem de laatste 5 unieke items
            final_links = found_list[-5:]
            print(f"   -> {len(final_links)} links gevonden via sitemap.")
            return final_links

        except Exception as e:
            print(f"   ❌ Fout bij VTM sitemap: {e}")
            return []

    # --- STANDAARD LOGICA VOOR ANDERE SITES (BeautifulSoup) ---
    try:
        # Gebruik curl_cffi met Chrome impersonation
        response = requests.get(site['url'], impersonate="chrome", timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')

        links = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            ignore_terms = ["/login", "/subscribe", "/media", "/search", "javascript", "mailto", "privacy", "cookie", "linkedin", "facebook", "twitter"]
            if any(x in href.lower() for x in ignore_terms): continue
            if href.startswith("/"): href = site['base'] + href

            if site['base'] in href:
                slug = href.replace(site['base'], "")
                if len(slug) > 20:
                    links.add(href)

        found_list = list(links)[:5]
        print(f"   -> {len(found_list)} links gevonden.")
        return found_list
    except Exception as e:
        print(f"   ❌ Fout bij ophalen links: {e}")
        return []

def extract_article_content(url):
    try:
        # Gebruik curl_cffi met Chrome impersonation
        response = requests.get(url, impersonate="chrome", timeout=15)
        # --- DEBUG REGELS ---
        print(f"   [DEBUG] Artikel Status Code: {response.status_code}")
        if "cloudflare" in response.text.lower() or "just a moment" in response.text.lower() or "turnstile" in response.text.lower():
            print("   🚨 [DEBUG] CLOUDFLARE BLOKKADE GEDETECTEERD OP DE ARTIKELPAGINA!")
        elif not soup.find('article') and not soup.find('div', class_=re.compile(r'Story_container')):
            print(f"   [DEBUG] Geen artikel gevonden. Ruwe HTML (eerste 500 tekens): {response.text[:500]}")
        # --------------------
        soup = BeautifulSoup(response.text, 'html.parser')

        article_node = soup.find('article')
        if not article_node:
            article_node = soup.find('div', class_=re.compile(r'Story_container'))

        if not article_node:
            print("   ⚠️ Geen artikel-content gevonden.")
            return None

        titel_tag = article_node.find('h1')
        titel = titel_tag.get_text(strip=True) if titel_tag else "Geen titel"

        datum_pub = datetime.now().strftime("%Y-%m-%d")
        datum_tag = soup.find('time')
        if datum_tag and datum_tag.has_attr('datetime'):
            datum_pub = datum_tag['datetime'][:10]

        for tag in article_node(["script", "style", "button", "iframe", "svg", "noscript"]):
            tag.decompose()

        text_content = []
        for p in article_node.find_all(['p', 'h2', 'h3', 'li']):
            text = p.get_text(strip=True)
            if len(text) > 15 and "Niet voor publicatie" not in text and "Persverantwoordelijke" not in text:
                text_content.append(text)

        full_text = "\n\n".join(text_content)
        return { "titel": titel, "tekst": full_text, "datum_publicatie": datum_pub }

    except Exception as e:
        print(f"   ❌ Fout bij lezen artikel: {e}")
        return None

def analyze_metadata(titel, tekst, url, source):
    if len(tekst) < 50: return None
    print(f"   🤖 AI Analyseert...")

    prompt = f"""
    Bron: {source}
    Titel: {titel}
    Tekst: {tekst[:4000]}

    TAAK:
    1. Gaat dit over een TV-PROGRAMMA?
    2. Is het voor een SPECIFIEKE AFLEVERING (type: "episode") of ALGEMENE info/SEIZOEN (type: "season")?
    3. Zoek de datum van de uitzending in de tekst.

    GEEF JSON:
    {{
      "programma_titel": "Titel van het programma",
      "match_type": "episode" of "season",
      "uitzend_datum": "YYYY-MM-DD" of null,
      "korte_intro": "Samenvatting (2-3 zinnen)",
      "ignore": false
    }}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content.replace("```json", "").replace("```", "").strip())
    except Exception as e:
        print(f"   ⚠️ AI Error: {e}")
        return None

def main():
    existing_data = []
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
                if content: existing_data = json.loads(content)
        except: 
            existing_data = []

    existing_urls = {item.get('original_url') for item in existing_data}
    new_entries = []

    print(f"📂 Huidige database bevat {len(existing_data)} items.")

    for site in SITES:
        links = get_recent_links(site)
        for link in links:
            if link in existing_urls: continue

            print(f"   🔍 Scrapen: {link}")
            content = extract_article_content(link)

            if content and len(content['tekst']) > 100:
                meta = analyze_metadata(content['titel'], content['tekst'], link, site['name'])
                if meta and not meta.get("ignore"):
                    entry = {
                        "id": f"{site['name']}-{int(time.time())}-{len(new_entries)}",
                        "zender": site['name'],
                        "programma": meta['programma_titel'],
                        "match_type": meta['match_type'],
                        "datum_uitzending": meta['uitzend_datum'],
                        "datum_publicatie": content['datum_publicatie'],
                        "titel_persbericht": content['titel'],
                        "intro": meta['korte_intro'],
                        "volledige_tekst": content['tekst'],
                        "original_url": link,
                        "scraped_at": datetime.now().isoformat()
                    }
                    print(f"   ✅ GEVONDEN: {entry['programma']} ({entry['match_type']})")
                    new_entries.append(entry)
                    existing_urls.add(link)
                else:
                    print("   ❌ AI: Irrelevant.")
            else:
                print(f"   ⚠️ Te weinig tekst gevonden of leeg artikel.")

            time.sleep(1)

    if new_entries:
        updated_data = new_entries + existing_data
        # Houd alleen de 100 meest recente items
        with open(JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(updated_data[:100], f, indent=2, ensure_ascii=False)
        print(f"💾 {len(new_entries)} items opgeslagen!")
    else:
        print("Geen nieuwe items om op te slaan.")

if __name__ == "__main__":
    main()
