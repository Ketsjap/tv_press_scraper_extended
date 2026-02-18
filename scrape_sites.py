import requests
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
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
        response = requests.get(site['url'], headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            
            # 1. Negeren van systeem-links
            ignore_terms = ["/login", "/subscribe", "/media", "/search", "javascript:", "mailto:", "prezly.com", "/privacy", "/cookie"]
            if any(x in href for x in ignore_terms):
                continue
            
            # 2. Maak absoluut
            if href.startswith("/"): 
                href = site['base'] + href
            
            # 3. Filter: Link moet lang genoeg zijn (korte links zijn vaak menu's: "Home", "Over ons")
            # Een persbericht-slug is meestal > 20 karakters
            if len(href.split('/')[-1]) > 15 and site['base'] in href:
                links.add(href)
        
        # Debug: Toon wat we gevonden hebben
        found_list = list(links)[:5]
        print(f"   -> {len(links)} links gevonden. We checken de eerste 5: {[l.split('/')[-1] for l in found_list]}")
        return found_list
    except Exception as e:
        print(f"   ❌ Fout bij ophalen links: {e}")
        return []

def extract_article_content(url):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Titel
        titel_tag = soup.find('h1')
        if not titel_tag: return None # Geen titel = geen artikel
        titel = titel_tag.get_text(strip=True)
        
        # Datum
        datum_pub = datetime.now().strftime("%Y-%m-%d")
        datum_tag = soup.find('time')
        if datum_tag and datum_tag.has_attr('datetime'):
            datum_pub = datum_tag['datetime'][:10]

        # Body
        body = soup.find('div', class_=re.compile(r'(story__body|content|prose)'))
        if not body: body = soup.body
            
        for tag in body(["script", "style", "nav", "footer", "button", "iframe"]):
            tag.decompose()
            
        full_text_parts = [p.get_text(strip=True) for p in body.find_all(['p', 'h2', 'li']) if len(p.get_text(strip=True)) > 10]
        full_text = "\n\n".join(full_text_parts)
        
        return { "titel": titel, "tekst": full_text, "datum_publicatie": datum_pub }
    except:
        return None

def analyze_metadata(titel, tekst, url, source):
    # Stuur intro naar AI
    prompt = f"""
    Bron: {source}
    Titel: {titel}
    Intro: {tekst[:1500]}
    
    TAAK:
    1. Gaat dit over een TV-PROGRAMMA?
    2. Is het voor een SPECIFIEKE AFLEVERING (type: "episode") of ALGEMENE info/SEIZOEN (type: "season")?
    
    GEEF JSON:
    {{
      "programma_titel": "Titel",
      "match_type": "episode" of "season",
      "uitzend_datum": "YYYY-MM-DD" of null,
      "korte_intro": "Samenvatting (2 zinnen)",
      "ignore": false (true als geen tv-nieuws)
    }}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content.replace("```json", "").replace("```", "").strip())
    except:
        return None

def main():
    # Zorg dat het bestand altijd bestaat, anders crasht Git
    existing_data = []
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r') as f: existing_data = json.load(f)
        except: existing_data = []
    else:
        # Maak leeg bestand aan als het niet bestaat
        with open(JSON_FILE, 'w') as f: json.dump([], f)

    existing_urls = {item.get('original_url') for item in existing_data}
    new_entries = []
    
    for site in SITES:
        links = get_recent_links(site)
        for link in links:
            if link in existing_urls: continue
            
            print(f"   🔍 Scrapen: {link}")
            content = extract_article_content(link)
            
            if content and len(content['tekst']) > 50:
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
                        "original_url": link
                    }
                    print(f"   ✅ GEVONDEN: {entry['programma']}")
                    new_entries.append(entry)
                    existing_urls.add(link)
                else:
                    print("   ❌ Irrelevant.")
            time.sleep(1)

    if new_entries:
        updated_data = new_entries + existing_data
        with open(JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(updated_data[:100], f, indent=2, ensure_ascii=False)
        print(f"💾 {len(new_entries)} items toegevoegd.")
    else:
        print("Geen nieuws.")

if __name__ == "__main__":
    main()
