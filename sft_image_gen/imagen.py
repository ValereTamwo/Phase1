import os
import json
import time
import re
from typing import List, Dict, Any
from pathlib import Path

from scripts import mix_marker_script, get_rect_script, label_marker_script
from html_tools import HtmlParser, print_html_object
# ---

from playwright.sync_api import sync_playwright, Error
import lxml.html
import random
from playwright.sync_api import Page, CDPSession, ViewportSize
from collections import defaultdict
from typing import Union

IN_VIEWPORT_RATIO_THRESHOLD = 0.6


def get_viewport_html(page: Page, client: CDPSession, viewport_size: ViewportSize) -> str:
    """
    Extrait le contenu HTML du body visible dans le viewport.
    
    Args:
        page: Instance de Page Playwright
        client: Instance de CDPSession Playwright
        viewport_size: Dimensions du viewport (largeur, hauteur)
    
    Returns:
        str: Contenu HTML du body visible dans le viewport
    """
    def get_browser_info() -> Dict[str, Any]:
        # Capture du snapshot DOM
        tree = client.send(
            "DOMSnapshot.captureSnapshot",
            {
                "computedStyles": [],
                "includeDOMRects": True,
                "includePaintOrder": True,
            },
        )
        
        # Calibration des bounds
        if not tree["documents"] or not tree["documents"][0]["layout"]["bounds"]:
             return {"DOMTree": tree, "config": {}}
             
        bounds = tree["documents"][0]["layout"]["bounds"]
        b = bounds[0]
        
        if viewport_size["width"] == 0:
            n = 1
        else:
            n = b[2] / viewport_size["width"]

        if n > 0:
            bounds = [[x / n for x in bound] for bound in bounds]
            tree["documents"][0]["layout"]["bounds"] = bounds

        # Extraction des infos du navigateur
        config = {
            "win_top_bound": page.evaluate("window.pageYOffset"),
            "win_left_bound": page.evaluate("window.pageXOffset"),
            "win_width": page.evaluate("window.screen.width"),
            "win_height": page.evaluate("window.screen.height"),
            "win_right_bound": page.evaluate("window.pageXOffset") + page.evaluate("window.screen.width"),
            "win_lower_bound": page.evaluate("window.pageYOffset") + page.evaluate("window.screen.height"),
            "device_pixel_ratio": page.evaluate("window.devicePixelRatio")
        }
        
        return {"DOMTree": tree, "config": config}

    def get_element_in_viewport_ratio(elem_left_bound: float, elem_top_bound: float, 
                                    width: float, height: float, config: Dict[str, Any]) -> float:
        elem_right_bound = elem_left_bound + width
        elem_lower_bound = elem_top_bound + height

        win_left_bound = 0
        win_right_bound = config["win_width"]
        win_top_bound = 0
        win_lower_bound = config["win_height"]

        overlap_width = max(0, min(elem_right_bound, win_right_bound) - max(elem_left_bound, win_left_bound))
        overlap_height = max(0, min(elem_lower_bound, win_lower_bound) - max(elem_top_bound, win_top_bound))
        
        return (overlap_width * overlap_height) / (width * height) if width * height > 0 else 0

    def fetch_page_html(info: Dict[str, Any]) -> list:
        tree = info["DOMTree"]
        config = info["config"]
        if not tree["documents"]: return []
        strings = tree["strings"]
        document = tree["documents"][0]
        nodes = document["nodes"]
        layout = document["layout"]
        
        dom_tree = []
        graph = {}
        
        for node_idx in range(len(nodes["nodeName"])):
            cur_node = {
                "nodeId": str(node_idx),
                "nodeType": strings[nodes["nodeType"][node_idx]] if nodes["nodeType"][node_idx] >= 0 else "generic",
                "nodeName": strings[nodes["nodeName"][node_idx]],
                "nodeValue": " ".join(strings[nodes["nodeValue"][node_idx]].split()) if nodes["nodeValue"][node_idx] >= 0 else "",
                "attributes": "",
                "backendNodeId": str(nodes["backendNodeId"][node_idx]),
                "parentId": str(nodes["parentIndex"][node_idx]),
                "childIds": [],
                "union_bound": None
            }

            # Traitement des attributs
            node_attributes = [strings[i] for i in nodes["attributes"][node_idx]]
            node_attributes_str = ""
            for i in range(0, len(node_attributes), 2):
                a, b = node_attributes[i], node_attributes[i + 1]
                b = " ".join([b_item for b_item in b.split() if 'vimium' not in b_item])
                node_attributes_str += f'{a}="{b}" '
            cur_node["attributes"] = node_attributes_str.strip()

            # Calcul des bounds
            if cur_node["parentId"] == "-1":
                cur_node["union_bound"] = [0.0, 0.0, 10.0, 10.0]
            else:
                bound = [0.0, 0.0, 0.0, 0.0]
                if node_idx in layout["nodeIndex"]:
                    bound = layout["bounds"][layout["nodeIndex"].index(node_idx)]
                    bound[0] -= config["win_left_bound"]
                    bound[1] -= config["win_top_bound"]
                cur_node["union_bound"] = bound

            dom_tree.append(cur_node)
            if cur_node["parentId"] != "-1":
                graph.setdefault(cur_node["parentId"], []).append(cur_node["nodeId"])

        # Ajout des relations parent-enfant
        for parent_id, child_ids in graph.items():
            if int(parent_id) < len(dom_tree):
                dom_tree[int(parent_id)]["childIds"] = child_ids

        # Filtrage des noeuds hors viewport
        def remove_node_in_graph(node):
            node_id = node["nodeId"]
            parent_id = node["parentId"]
            child_ids = node["childIds"]
            
            if parent_id != "-1" and int(parent_id) < len(dom_tree):
                if node_id in dom_tree[int(parent_id)]["childIds"]:
                    index = dom_tree[int(parent_id)]["childIds"].index(node_id)
                    dom_tree[int(parent_id)]["childIds"].pop(index)
                    dom_tree[int(parent_id)]["childIds"][index:index] = child_ids
                    for child_id in child_ids:
                        if int(child_id) < len(dom_tree):
                            dom_tree[int(child_id)]["parentId"] = parent_id
            if int(node_id) < len(dom_tree):
                dom_tree[int(node_id)]["parentId"] = "[REMOVED]"

        config = info["config"]
        for node in dom_tree:
            if not node["union_bound"] or (node["union_bound"][2] == 0.0 or node["union_bound"][3] == 0.0):
                if node["nodeName"] not in ['OPTION'] or (node["parentId"] != "-1" and int(node["parentId"]) < len(dom_tree) and dom_tree[int(node["parentId"])]["nodeName"] not in ["SELECT"]):
                    remove_node_in_graph(node)
                    continue
                
            in_viewport_ratio = get_element_in_viewport_ratio(
                node["union_bound"][0], node["union_bound"][1],
                node["union_bound"][2], node["union_bound"][3], config
            )
            
            if in_viewport_ratio < IN_VIEWPORT_RATIO_THRESHOLD:
                remove_node_in_graph(node)

        return [node for node in dom_tree if node["parentId"] != "[REMOVED]"]

    def parse_html(dom_tree: list) -> str:
        if not dom_tree: return "<body></body>"
        nodeid_to_cursor = {node["nodeId"]: idx for idx, node in enumerate(dom_tree)}
        
        def dfs(node_cursor: int) -> str:
            node = dom_tree[node_cursor]
            if node["nodeName"] == "#text":
                node["nodeName"] = "text"
                
            node_str = f"<{node['nodeName']}"
            if node["attributes"]:
                node_str += f" {node['attributes']}"
            node_str += f">{node['nodeValue']}"
            
            try:
                tree_str = node_str if node["attributes"] or node["nodeValue"] else ""
                for child_id in node["childIds"]:
                    if child_id in nodeid_to_cursor:
                        tree_str += dfs(nodeid_to_cursor[child_id])
                if node["attributes"] or node["nodeValue"]:
                    tree_str += f"</{node['nodeName']}>"
                return tree_str
            except:
                return ""

        html = dfs(0)
        try:
            # Validation du HTML
            lxml.html.fromstring(html)
            return html
        except:
            return "<body></body>"

    try:
        start_time = time.time()
        browser_info = get_browser_info()
        print(f'[browser_info] {time.time() - start_time:.3f}s')
        
        start_time = time.time()
        dom_tree = fetch_page_html(browser_info)
        print(f'[fetch_html] {time.time() - start_time:.3f}s')
        
        start_time = time.time()
        html_content = parse_html(dom_tree)
        print(f'[parse_html] {time.time() - start_time:.3f}s')
        
        return html_content
        
    except Exception as e:
        print(f"Erreur lors de l'extraction HTML: {str(e)}")
        return "<body></body>"


def url_to_dirname(url: str) -> str:
    """Convertit une URL ou un nom de fichier en un nom de dossier valide."""
    if url.startswith("file://"):
        # C'est un fichier local, on extrait le nom du fichier
        # ex: file:///path/to/chunk_1_conv_1.html -> chunk_1_conv_1.html
        name = Path(url).stem # -> chunk_1_conv_1 (sans .html)
    else:
        name = re.sub(r'https?://(www\.)?', '', url)
    
    name = re.sub(r'[^a-zA-Z0-9\._-]', '_', name)
    return name[:100]



def generate_data_for_url(page: Page, url: str, output_dir: str, client: CDPSession, viewport: ViewportSize) -> bool:
    """
    Génère les données d'alignement pour une URL (web ou locale), avec une sortie formalisée.
    Crée un dossier par source contenant les images (brute, marquée) et un JSON.
    """
    try:
        wait_state = 'load' if url.startswith("file://") else 'networkidle'
        print(f"Processing source: {url}")
        page.goto(url, timeout=60000)
        page.wait_for_load_state(wait_state, timeout=60000)
        time.sleep(1)
    except Error as e:
        print(f"Error navigating to {url}: {e}")
        return False

    url_folder_name = url_to_dirname(url)
    url_output_path = os.path.join(output_dir, url_folder_name)
    os.makedirs(url_output_path, exist_ok=True)
    print(f"  -> Saving data to: {url_output_path}")

    page.evaluate(mix_marker_script)
    page.wait_for_timeout(100)
    
    start_id = 0
    elem_items, start_id = page.evaluate(get_rect_script, {
        "selector": ".possible-clickable-element",
        "startIndex": start_id
    })
    
    raw_image_path = os.path.join(url_output_path, 'raw_image.png')
    page.screenshot(path=raw_image_path)
    
    # --- 5. Récupération et parsing de l'HTML du viewport ---
    raw_viewport_html = get_viewport_html(page=page, client=client, viewport_size=viewport)
    
    # --- 4. Marquage des éléments sur la page et capture de l'image MARQUÉE ---
    page.evaluate(label_marker_script, elem_items)
    marked_image_path = os.path.join(url_output_path, 'marked_image.png')
    page.screenshot(path=marked_image_path)
    
    # Configuration pour le parser
    parser_args = {
        'use_position': False,
        'id_attr': 'backend-id',
        'label_generator': 'order',
        'label_attr': 'data-testid',
        'attr_list': ['title', 'value', 'placeholder', 'selected'],
        'prompt': 'refine',
    }
    
    parser = HtmlParser(ctx=raw_viewport_html, args=parser_args)
    parsed_result = parser.parse_tree()
    # Utilisation de print_html_object pour obtenir un HTML "pretty-printed"
    parsed_viewport_html = print_html_object(parsed_result['html'])

    # --- 6. Assemblage et sauvegarde du fichier JSON ---
    output_data = {
        "source_url": url,
        "raw_html": raw_viewport_html,
        "parsed_html": parsed_viewport_html,
        "items": elem_items
    }
    
    json_path = os.path.join(url_output_path, 'data.json')
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=4)
        print(f"  -> Successfully saved data for source: {Path(url).name}")
    except Exception as e:
        print(f"  -> Error saving JSON for {Path(url).name}: {e}")
        return False

    return True

def main():

    INPUT_HTML_DIR = "sft_html"
    # Le dossier où les résultats seront sauvegardés
    OUTPUT_DIRECTORY = "alignment_dataset_from_local_html"

    # --- Étape 1: Trouver tous les fichiers HTML à traiter ---
    if not os.path.isdir(INPUT_HTML_DIR):
        print(f"Erreur: Le dossier d'entrée '{INPUT_HTML_DIR}' n'a pas été trouvé.")
        return

    html_files = [f for f in os.listdir(INPUT_HTML_DIR) if f.endswith('.html')]

    if not html_files:
        print(f"Aucun fichier .html n'a été trouvé dans le dossier '{INPUT_HTML_DIR}'.")
        return

    # Conversion des noms de fichiers en URLs locales que Playwright peut ouvrir
    # On utilise Path.resolve() pour obtenir le chemin absolu
    sources_to_process = [
        Path(os.path.join(INPUT_HTML_DIR, f)).resolve().as_uri()
        for f in html_files
    ]
    # as_uri() crée une URL correcte comme 'file:///C:/path/to/your/file.html'

    print(f"{len(sources_to_process)} fichiers HTML trouvés et prêts à être traités.")
    random.shuffle(sources_to_process)

    # --- Création du dossier de sortie ---
    if not os.path.exists(OUTPUT_DIRECTORY):
        os.makedirs(OUTPUT_DIRECTORY)

    # --- Lancement du Navigateur ---
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Définition explicite du viewport
        viewport_size = {"width": 1280, "height": 720}
        context = browser.new_context(viewport=viewport_size)
        page = context.new_page()
        client = page.context.new_cdp_session(page)

        # --- Boucle sur les sources (qui sont maintenant des URLs locales) ---
        for source_url in sources_to_process:
            generate_data_for_url(  # On appelle la même fonction !
                page=page, 
                url=source_url, 
                output_dir=OUTPUT_DIRECTORY,
                client=client,
                viewport=viewport_size
            )
            # Une pause plus courte peut suffire pour les fichiers locaux
            time.sleep(0.5) 

        browser.close()

if __name__ == "__main__":
    main()