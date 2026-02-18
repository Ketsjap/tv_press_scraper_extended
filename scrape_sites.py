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
        # Gebruik een 'echte' browser User-Agent
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        response = requests.get(site['url'], headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            # Filter op typische artikel-structuren, negeer systeem-links
            ignore_terms = ["/login", "/subscribe", "/media", "/search", "javascript", "mailto", "privacy", "cookie", "linkedin", "facebook", "twitter"]
            
            if any(x in href.lower() for x in ignore_terms): continue
            
            # Maak link compleet
            if href.startswith("/"): href = site['base'] + href
            
            # Alleen links die op het domein zelf blijven
            if site['base'] in href:
                # Check of het een "diepe" link is (niet de homepage zelf)
                slug = href.replace(site['base'], "")
                if len(slug) > 20: # Korte links zijn vaak menu items
                    links.add(href)
        
        # Pak de eerste 5 unieke links
        found_list = list(links)[:5]
        print(f"   -> {len(links)} links gevonden. We checken: {[l.split('/')[-1][:20] for l in found_list]}")
        return found_list
    except Exception as e:
        print(f"   ❌ Fout bij ophalen links: {e}")
        return []

def extract_article_content(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 🔥 CRUCIAAL: Zoek de <article> tag. 
        # In de code die je stuurde staat alles binnen <article>.
        # Alles daarbuiten (zoals 'Meest recente verhalen') negeren we.
        article_node = soup.find('article')
        
        # Fallback als er geen article tag is (zou niet mogen bij Prezly)
        if not article_node: 
            article_node = soup.find('div', class_=re.compile(r'Story_container'))
        
        if not article_node:
            print("   ⚠️ Geen artikel-content gevonden.")
            return None

        # 1. Titel (H1 staat meestal in de article)
        titel_tag = article_node.find('h1')
        titel = titel_tag.get_text(strip=True) if titel_tag else "Geen titel"

        # 2. Datum
        datum_pub = datetime.now().strftime("%Y-%m-%d")
        # Soms staat datum net buiten de article in de header wrapper
        datum_tag = soup.find('time') 
        if datum_tag and datum_tag.has_attr('datetime'):
            datum_pub = datum_tag['datetime'][:10]

        # 3. Schoonmaak van het artikel
        for tag in article_node(["script", "style", "button", "iframe", "svg", "noscript"]):
            tag.decompose()

        # 4. Tekst extractie (Paragrafen en kopjes)
        text_content = []
        for p in article_node.find_all(['p', 'h2', 'h3', 'li']):
            text = p.get_text(strip=True)
            # Filter boilerplates
            if len(text) > 15 and "Niet voor publicatie" not in text and "Persverantwoordelijke" not in text:
                text_content.append(text)
        
        full_text = "\n\n".join(text_content)
        
        return { "titel": titel, "tekst": full_text, "datum_publicatie": datum_pub }

    except Exception as e:
        print(f"   ❌ Fout bij lezen artikel: {e}")
        return None

def analyze_metadata(titel, tekst, url, source):
    # Als tekst te kort is, heeft het geen zin
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
      "programma_titel": "Titel van het programma (zonder 'seizoen x')",
      "match_type": "episode" of "season",
      "uitzend_datum": "YYYY-MM-DD" of null (Als er staat 'vanaf 2 maart', vul 202X-03-02 in. Als er staat 'vanavond' en publicatiedatum is vandaag, vul vandaag in.),
      "korte_intro": "Samenvatting (2-3 zinnen)",
      "ignore": false (zet true als dit GEEN tv-nieuws is)
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
        except: existing_data = []
    
    # We slaan de URL op om dubbels te voorkomen
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
        with open(JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(updated_data[:100], f, indent=2, ensure_ascii=False)
        print(f"💾 {len(new_entries)} items opgeslagen!")
    else:
        print("Geen nieuwe items om op te slaan.")

if __name__ == "__main__":
    main()
