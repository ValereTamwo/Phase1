from playwright.sync_api import sync_playwright
from html_tools import HtmlParser
import json

def collect_data(url: str, output_file="donnees_phase1.json"):
    donnees_pour_phase_1 = []

    with sync_playwright() as p:
        # 1. Lancer navigateur (Chromium headless)
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 2. Aller à l’URL
        page.goto(url, timeout=60000)

        # Screenshot
        screenshot_path = "page.png"
        page.screenshot(path=screenshot_path)

        # HTML brut
        html_brut = page.content()

        # 3. Initialiser parser
        parser = HtmlParser(ctx=html_brut, args={})
        parser.parse_tree()

        # 4. Récupération des mappings
        map_id_vers_xpath = parser.bids2xpath

        # 5. Boucler sur éléments
        for element_bid, element_xpath in map_id_vers_xpath.items():
            if not element_xpath.startswith('/'): 
                continue  # Garder seulement les vrais XPaths

            try:
                # a. Obtenir bounding box
                bbox = page.locator(f"xpath={element_xpath}").bounding_box()
                if not bbox:
                    continue
            except Exception:
                continue

            # b. Texte parsé
            texte_parse = parser.get_segment(element_bid)

            # c. Stocker données
            donnees_pour_phase_1.append({
                "screenshot_path": screenshot_path,
                "element_id": element_bid,
                "parsed_text": texte_parse,
                "coordinates": bbox
            })

        # 6. Sauvegarder en JSON
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(donnees_pour_phase_1, f, indent=2, ensure_ascii=False)

        browser.close()

    print(f"✅ Extraction terminée. Résultats sauvegardés dans {output_file}")

# Exemple d’utilisation
if __name__ == "__main__":
    collect_data("https://wikipedia.com")
