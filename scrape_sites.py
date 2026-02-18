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

# De pers-sites (Prezly platform)
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
        # Prezly sites tonen stories vaak in blocks. We zoeken alle links.
        for a in soup.find_all('a', href=True):
            href = a['href']
            # Filter op relevante url-patronen voor persberichten
            if any(x in href for x in ["/story/", "/persbericht/", "/press-release/", "/nieuws/"]):
                # Negeren van tags, archieven of logins
                if any(x in href for x in ["/tag/", "/login", "/subscribe"]):
                    continue
                
                if href.startswith("/"): 
                    href = site['base'] + href
                links.add(href)
        
        # We pakken de 6 'nieuwste' (bovenste) links om tokens te sparen
        return list(links)[:6]
    except Exception as e:
        print(f"   ❌ Fout bij ophalen links: {e}")
        return []

def extract_article_content(url):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Titel
        titel_tag = soup.find('h1')
        titel = titel_tag.get_text(strip=True) if titel_tag else "Geen titel"
        
        # 2. Datum (Prezly metadata in <time>)
        datum_tag = soup.find('time')
        if datum_tag and datum_tag.has_attr('datetime'):
            datum_pub = datum_tag['datetime'][:10] # YYYY-MM-DD
        else:
            datum_pub = datetime.now().strftime("%Y-%m-%d")

        # 3. Inhoud (Zoek de body)
        # Prezly gebruikt vaak 'story__body' of 'content'
        body = soup.find('div', class_=re.compile(r'(story__body|content|prose)'))
        if not body: body = soup.body
            
        # Verwijder rommel (scripts, knoppen, share buttons)
        for tag in body(["script", "style", "nav", "footer", "header", "button", "iframe", "svg"]):
            tag.decompose()
            
        # Tekst extractie met behoud van alinea's
        # We bouwen een schone tekst op
        full_text_parts = []
        for elem in body.find_all(['p', 'h2', 'h3', 'li']):
            tekst = elem.get_text(strip=True)
            if len(tekst) > 5: # Filter lege regels
                full_text_parts.append(tekst)
        
        full_text = "\n\n".join(full_text_parts)
        
        return {
            "titel": titel,
            "tekst": full_text,
            "datum_publicatie": datum_pub
        }
    except Exception as e:
        print(f"   ❌ Fout bij lezen artikel: {e}")
        return None

def analyze_metadata(titel, tekst, url, source):
    # We sturen de intro naar AI voor classificatie
    intro_text = tekst[:2000] 
    
    prompt = f"""
    Analyseer dit persbericht van {source}.
    
    TITEL: {titel}
    INTRO: {intro_text}
    
    JOUW TAAK:
    1. Over welk TV-PROGRAMMA gaat dit?
    2. Is dit info over één SPECIFIEKE AFLEVERING (bijv. "In aflevering 3", "Morgen", "Dinsdag 20/02") -> type: "episode"
    3. Of is dit ALGEMENE info (bijv. "Nieuw seizoen start", "Dit zijn de kandidaten", "Programma X is terug") -> type: "season"
    
    GEEF JSON:
    {{
      "programma_titel": "Titel (bv: De Mol)",
      "match_type": "episode" of "season",
      "uitzend_datum": "YYYY-MM-DD" (Alleen invullen als match_type='episode' EN er een datum in de tekst staat. Anders null.),
      "korte_intro": "De eerste 2-3 zinnen van het artikel als samenvatting.",
      "ignore": false (zet true als dit GEEN tv-programma is, bv. bedrijfsresultaten)
    }}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        raw = response.choices[0].message.content.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"   AI Error: {e}")
        return None

def main():
    # 1. Laad bestaande data (History behouden!)
    existing_data = []
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r') as f: existing_data = json.load(f)
        except: pass
    
    # Lijst van URL's die we al hebben (om dubbels te voorkomen)
    existing_urls = {item.get('original_url') for item in existing_data}
    new_entries = []
    
    # 2. Loop door de sites
    for site in SITES:
        links = get_recent_links(site)
        for link in links:
            if link in existing_urls:
                continue # Hebben we al
            
            print(f"   🔍 Scrapen: {link}")
            content = extract_article_content(link)
            
            if content and len(content['tekst']) > 50:
                # 3. AI Analyse
                meta = analyze_metadata(content['titel'], content['tekst'], link, site['name'])
                
                if meta and not meta.get("ignore"):
                    # 4. Data samenstellen
                    entry = {
                        "id": f"{site['name']}-{int(time.time())}-{len(new_entries)}", # Unieke ID
                        "zender": site['name'],
                        "programma": meta['programma_titel'],
                        "match_type": meta['match_type'],
                        "datum_uitzending": meta['uitzend_datum'],
                        "datum_publicatie": content['datum_publicatie'],
                        "titel_persbericht": content['titel'],
                        "intro": meta['korte_intro'],
                        "volledige_tekst": content['tekst'], # De VOLLEDIGE tekst
                        "original_url": link,
                        "scraped_at": datetime.now().isoformat()
                    }
                    
                    print(f"   ✅ GEVONDEN: {entry['programma']} ({entry['match_type']})")
                    new_entries.append(entry)
                    existing_urls.add(link)
                else:
                    print("   ❌ Irrelevant of geen TV-programma.")
            
            time.sleep(1) # Rustig aan met de server

    # 5. Opslaan (Nieuwe bovenaan + Oude behouden)
    if new_entries:
        updated_data = new_entries + existing_data
        # Houd het archief beheersbaar (bv. max 150 items)
        updated_data = updated_data[:150]
        
        with open(JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(updated_data, f, indent=2, ensure_ascii=False)
        print(f"💾 {len(new_entries)} nieuwe items toegevoegd aan press.json")
    else:
        print("Geen nieuwe persberichten gevonden.")

if __name__ == "__main__":
    main()
