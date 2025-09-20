import os
import json
import time
import re
from typing import List, Dict, Tuple, Any
from lxml import html
from playwright.sync_api import sync_playwright, Page, Error

# ==============================================================================
#  Fonction de Parsing et d'Identification (Inspirée de html-tools/utils.py)
# ==============================================================================

def get_xpath_top_down(
    element: html.HtmlElement,
    id_column: str = 'temp_id',
    path: str = '',
    order: int = 0,
    in_svg: bool = False,
    temp_id_counter: list = [0]
) -> Dict[str, str]:
    """
    Parcourt l'arbre DOM, assigne un ID unique à chaque élément et retourne un
    dictionnaire mappant chaque ID à son XPath complet.
    """
    i2xpath = {}
    tag = element.tag.lower()
    in_svg = in_svg or (tag == 'svg')

    # Construit le XPath de l'élément actuel
    if not in_svg and 'id' in element.attrib:
        node_id = element.attrib['id']
        path = f'//*[@id="{node_id}"]'
    else:
        suffix = f'[{order}]' if order > 0 else ''
        prefix = f'*[name()="{tag}"]' if in_svg else tag
        path = path + '/' + prefix + suffix
    
    # Assigne un ID unique et l'ajoute au dictionnaire
    current_id = str(temp_id_counter[0])
    element.attrib[id_column] = current_id
    i2xpath[current_id] = path
    temp_id_counter[0] += 1
    
    # Appel récursif sur les enfants
    children = element.getchildren()
    tag_dict = {}
    for child in children:
        ctag = child.tag.lower()
        tag_dict.setdefault(ctag, 0)
        tag_dict[ctag] += 1
    
    id_list = [tag_dict[child.tag.lower()] for child in children]
    
    for cid, child in zip(id_list, children):
        ctag = child.tag.lower()
        # L'ordre est 1-indexé dans XPath, donc on utilise cid. L'ordre n'est pertinent que s'il y a plusieurs enfants avec la même balise.
        child_order = cid if tag_dict[ctag] > 1 else 0
        child_i2xpath = get_xpath_top_down(child, id_column, path, child_order, in_svg, temp_id_counter)
        i2xpath.update(child_i2xpath)
    
    return i2xpath

# ==============================================================================
#  Fonction Principale de Génération de Données
# ==============================================================================

def clean_html(ctx: str) -> str:
    """Nettoie le HTML en retirant les scripts, styles, et commentaires."""
    ctx = re.sub('<!--[\\s\\S]*?-->', '', ctx, flags=re.DOTALL)
    ctx = re.sub('<style[\\s\\S]*?>[\\s\\S]*?</style>', '', ctx, flags=re.DOTALL)
    ctx = re.sub('<script[\\s\\S]*?>[\\s\\S]*?</script>', '', ctx, flags=re.DOTALL)
    return re.sub(r'\s+', ' ', ctx).strip()

def generate_data_for_url(page: Page, url: str, output_dir: str) -> bool:
    """
    Génère les données d'alignement pour une seule URL.
    Sauvegarde un screenshot et un fichier .jsonl avec les annotations.
    """
    try:
        print(f"Processing URL: {url}")
        page.goto(url, timeout=60000)
        page.wait_for_load_state('networkidle', timeout=60000)
        time.sleep(3) # Attente supplémentaire pour le rendu dynamique
    except Error as e:
        print(f"Could not navigate to {url}: {e}")
        return False

    # --- 1. Nettoyage et Parsing du HTML ---
    html_content = page.content()
    cleaned_html = clean_html(html_content)
    if not cleaned_html:
        print(f"Empty HTML for {url}")
        return False
    dom_tree = html.fromstring(cleaned_html)

    # --- 2. Attribution des ID et XPaths ---
    # Le compteur est dans une liste pour être mutable et passé par référence
    temp_id_counter = [0]
    id_to_xpath_map = get_xpath_top_down(dom_tree, temp_id_counter=temp_id_counter)

    # --- 3. Prise du Screenshot ---
    domain_name = re.sub(r'https?://(www\.)?', '', url).split('/')[0]
    screenshot_filename = f"{domain_name}_{int(time.time())}.png"
    screenshot_path = os.path.join(output_dir, screenshot_filename)
    page.screenshot(path=screenshot_path, full_page=True)

    # --- 4. Génération des Annotations (Coordonnées) ---
    alignment_data = []
    print(f"Found {len(id_to_xpath_map)} elements to process.")
    
    for element_id, xpath in id_to_xpath_map.items():
        try:
            locator = page.locator(f"xpath={xpath}")
            # Ne considérer que le premier élément s'il y en a plusieurs
            if locator.count() == 0:
                continue

            element = locator.first
            if not element.is_visible():
                continue

            bbox = element.bounding_box()
            if bbox is None or bbox['width'] == 0 or bbox['height'] == 0:
                continue

            # Ici, on pourrait utiliser la logique de `HtmlPrompt` pour générer le texte exact.
            # Pour simplifier, nous extrayons le texte brut de l'élément.
            # Pour une implémentation complète, il faudrait intégrer la logique de `get_segment`.
            node = dom_tree.xpath(xpath)
            if not node: continue
            parsed_text = (node[0].text_content() or "").strip().replace("\n", " ")[:200]
            
            # L'annotation finale pour cet élément
            annotation = {
                "screenshot_file": screenshot_filename,
                "element_id": element_id,
                "parsed_text": parsed_text, # Simplification, idéalement utiliser votre `get_segment`
                "xpath": xpath,
                "coordinates": bbox # {'x': float, 'y': float, 'width': float, 'height': float}
            }
            alignment_data.append(annotation)

        except Error as e:
            # Ignorer les éléments qui ne peuvent être localisés
            pass

    # --- 5. Sauvegarde des données annotées ---
    if not alignment_data:
        print(f"No visible elements with bounding boxes found for {url}")
        os.remove(screenshot_path) # Nettoyer le screenshot si aucune donnée n'est générée
        return False
        
    output_jsonl_path = os.path.join(output_dir, f"{domain_name}_{int(time.time())}.jsonl")
    with open(output_jsonl_path, 'w', encoding='utf-8') as f:
        for item in alignment_data:
            f.write(json.dumps(item) + '\n')
    
    print(f"Successfully generated {len(alignment_data)} data points for {url}. Saved to {output_jsonl_path}")
    return True

def main():
    # --- Configuration ---
    URLS_TO_PROCESS = [
        "https://datacamp.com/"
        "https://en.wikipedia.org/wiki/Main_Page",
        "https://github.com/",
        "https://www.amazon.com/",
        "https://www.reddit.com/",
        # Ajoutez ici la liste des URLs que vous voulez scanner
    ]
    OUTPUT_DIRECTORY = "alignment_dataset"

    if not os.path.exists(OUTPUT_DIRECTORY):
        os.makedirs(OUTPUT_DIRECTORY)

    # --- Lancement du Navigateur ---
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for url in URLS_TO_PROCESS:
            generate_data_for_url(page, url, OUTPUT_DIRECTORY)
            time.sleep(2) # Petite pause entre les sites

        browser.close()

if __name__ == "__main__":
    main()