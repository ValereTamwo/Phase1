import os
import json
import time
import re
from typing import List, Dict, Any
from scripts import *
# Importation directe depuis votre dossier d'outils
from html_tools import HtmlParser
from playwright.sync_api import sync_playwright, Error
import lxml.html
    
import random

from html_tools import print_html_object

# Mettez ces imports en haut de votre fichier
from playwright.sync_api import Page, CDPSession,ViewportSize
from collections import defaultdict
from typing import Any, Dict, List, TypedDict, Union
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
        bounds = tree["documents"][0]["layout"]["bounds"]
        b = bounds[0]
        n = b[2] / viewport_size["width"]
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
            dom_tree[int(parent_id)]["childIds"] = child_ids

        # Filtrage des noeuds hors viewport
        def remove_node_in_graph(node):
            node_id = node["nodeId"]
            parent_id = node["parentId"]
            child_ids = node["childIds"]
            
            if parent_id != "-1":
                index = dom_tree[int(parent_id)]["childIds"].index(node_id)
                dom_tree[int(parent_id)]["childIds"].pop(index)
                dom_tree[int(parent_id)]["childIds"][index:index] = child_ids
                for child_id in child_ids:
                    dom_tree[int(child_id)]["parentId"] = parent_id
            dom_tree[int(node_id)]["parentId"] = "[REMOVED]"

        config = info["config"]
        for node in dom_tree:
            if not node["union_bound"] or (node["union_bound"][2] == 0.0 or node["union_bound"][3] == 0.0):
                if node["nodeName"] not in ['OPTION'] or (node["parentId"] != "-1" and dom_tree[int(node["parentId"])]["nodeName"] not in ["SELECT"]):
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

# ==============================================================================
#  Fonction utilitaire pour nettoyer l'URL
# ==============================================================================
def url_to_dirname(url: str) -> str:
    """Convertit une URL en un nom de dossier valide."""
    # Enlève le protocole
    name = re.sub(r'https?://(www\.)?', '', url)
    # Remplace les caractères non-alphanumériques par des underscores
    name = re.sub(r'[^a-zA-Z0-9\._-]', '_', name)
    # Tronque si le nom est trop long
    return name[:100]



def generate_data_for_url(page: Page, url: str, output_dir: str, client: CDPSession, viewport: ViewportSize) -> bool:
    """
    Génère les données d'alignement pour une URL, avec une sortie formalisée.
    Crée un dossier par URL contenant les images (brute, marquée) et un JSON.
    """
    try:
        print(f"Processing URL: {url}")
        page.goto(url, timeout=60000)
        page.wait_for_load_state('networkidle', timeout=60000)
        time.sleep(3)
    except Error as e:
        print(f"Error navigating to {url}: {e}")
        return False

    # --- 1. Création du dossier de sortie pour l'URL ---
    url_folder_name = url_to_dirname(url)
    url_output_path = os.path.join(output_dir, url_folder_name)
    os.makedirs(url_output_path, exist_ok=True)
    print(f"  -> Saving data to: {url_output_path}")

    # --- 2. Injection des scripts et récupération des éléments ---
    page.evaluate(mix_marker_script)
    page.wait_for_timeout(100)
    
    start_id = 0
    elem_items, start_id = page.evaluate(get_rect_script, {
        "selector": ".possible-clickable-element",
        "startIndex": start_id
    })
    
    # --- 3. Capture de l'image BRUTE (avant marquage) ---
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
        "raw_html": raw_viewport_html,
        "parsed_html": parsed_viewport_html,
        "items": elem_items
    }
    
    json_path = os.path.join(url_output_path, 'data.json')
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=4)
        print(f"  -> Successfully saved data for {url}")
    except Exception as e:
        print(f"  -> Error saving JSON for {url}: {e}")
        return False

    return True

def main():
    # --- Configuration ---
    URLS_TO_PROCESS = [
        "https://en.wikipedia.org/wiki/Quantum_computing",
        "https://github.com/THUDM/WebRL",
        "https://www.amazon.fr/s?k=python+programming",
        "https://www.reddit.com/r/MachineLearning/",
    ]
    # J'ai changé le nom du dossier pour ne pas écraser vos anciens résultats
    OUTPUT_DIRECTORY = "alignment_dataset_formalized"


    FINAL_URLS_TO_PROCESS = [
        # Search Engines
        "google.com", "bing.com", "yahoo.com", "baidu.com", "yandex.ru",
        "mail.ru", "duckduckgo.com", "ask.com", "aol.com", "seznam.cz",

        # Social & Messaging
        "facebook.com", "instagram.com", "twitter.com", "linkedin.com",
        "whatsapp.com", "telegram.org", "tiktok.com", "snapchat.com",
        "reddit.com", "vk.com", "discord.com", "wechat.com", "messenger.com",
        "x.com", "t.me", "pinterest.com", "tumblr.com", "medium.com",
        "chatgpt.com", "twitch.tv", "line.me", "ok.ru", "vkontakte.ru",
        "weibo.com", "qq.com", "clubhouse.com", "hike.in", "kakaocorp.com", "signal.org",

        # Technology & Software
        "microsoft.com", "apple.com", "github.com", "gitlab.com", "oracle.com",
        "adobe.com", "ubuntu.com", "mozilla.org", "docker.com", "cloudflare.com",
        "nginx.org", "apache.org", "nodejs.org", "python.org", "kubernetes.io",
        "terraform.io", "jetbrains.com", "zoom.us", "slack.com", "salesforce.com",
        "trello.com", "asana.com", "notion.so", "postman.com", "heroku.com",
        "digitalocean.com", "stripe.com", "paypal.com", "atlassian.com", "figma.com",
        "canva.com", "wordpress.org", "wordpress.com", "wixsite.com", "shopify.com",
        "medium.com", "weebly.com", "tumblr.com", "drupal.org", "ghost.org", "bitbucket.org",

        # CDN & Hosting
        "akamai.net", "cloudfront.net", "fastly.net", "cdninstagram.com", "gstatic.com",
        "googlesyndication.com", "googletagmanager.com", "ytimg.com", "akamaized.net",
        "edgekey.net", "cdn-apple.com", "rbxcdn.com", "rocket-cdn.com", "spov-msedge.net",
        "aiv-cdn.net", "dual-s-msedge.net", "cloudflare.net", "cloudflare-dns.com",
        "trbcdn.net", "bytefcdn-oversea.com", "tiktokcdn.com", "tiktokcdn-us.com",
        "pv-cdn.net", "b-cdn.net", "vecdnlb.com", "vedcdnlb.com", "ln-msedge.net",
        "spv-msedge.net", "a-msedge.net", "l-msedge.net", "wac-msedge.net",

        # Entertainment & Streaming
        "youtube.com", "youtube-nocookie.com", "netflix.com", "spotify.com",
        "vimeo.com", "twitch.tv", "steamcommunity.com", "epicgames.com", "playstation.net",
        "nflxso.net", "hulu.com", "disneyplus.com", "soundcloud.com", "moe.video",
        "ivi.ru", "kwai-pro.com", "capcutapi.com", "tiktokv.com", "ttvnw.net", "amazonvideo.com",

        # Web Platforms & Blogging
        "blogspot.com", "medium.com", "wordpress.com", "tumblr.com", "wixsite.com",
        "weebly.com", "ghost.org", "drupal.org", "notion.so", "figma.com",
        "github.io", "linktr.ee", "launchpad.net", "sourceforge.net", "example.com",
        "ui.com", "omtrdc.net", "adobe.io", "launchdarkly.com", "appsflyer.com",
        "appsflyersdk.com", "app-analytics-services.com", "app-measurement.com", "capcutapi.com", "shalltry.com",

        # News & Media
        "nytimes.com", "cnn.com", "bbc.co.uk", "bbc.com", "theguardian.com",
        "forbes.com",

        # Education & Reference
        "w3.org", "mit.edu", "harvard.edu", "sciencedirect.com", "nih.gov",
        "europea.eu", "doi.org", "creativecommons.org", "wikimedia.org",
        "nist.gov", "3gppnetwork.org", "ipv4only.arpa", "who.int", "openai.com",

        # Government & Services
        "windowsupdate.com", "msftconnecttest.com", "www.gov.uk", "ripn.net",
        "registrar-servers.com", "root-servers.net",

        # Networking & Telecom
        "telekom.de", "mikrotik.com", "ksyuncdn.com", "vecdnlb.com", "ipv4only.arpa",
        "dnsowl.com",

        # Advertising & Marketing
        "doubleclick.net", "adnxs.com", "criteo.com", "rubiconproject.com",
        "adriver.ru", "2mdn.net", "pubmatic.com", "adtrafficquality.google",
        "taboola.com", "demdex.net", "omtrdc.net", "adsrvr.org", "vungle.com",
        "crashlytics.com",

        # Finance & Payments
        "paypal.com", "stripe.com", "intuit.com", "samsungqbe.com",

        # Commerce & Retail
        "amazon.com", "amazon.co.uk", "ebay.com", "wildberries.ru", "ozon.ru",
        "alibabadns.com", "samsung.com", "xiaomi.com", "hp.com", "apple.com",
        "shopify.com", "canva.com", "vecdnlb.com"
    ]

    sites = ["https://" + url for url in FINAL_URLS_TO_PROCESS]

    random.shuffle(sites)



    if not os.path.exists(OUTPUT_DIRECTORY):
        os.makedirs(OUTPUT_DIRECTORY)

    # --- Lancement du Navigateur ---
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Définition explicite du viewport pour être cohérent
        viewport_size = {"width": 1280, "height": 720}
        context = browser.new_context(viewport=viewport_size)
        page = context.new_page()
        client = page.context.new_cdp_session(page)

        for url in sites:
            generate_data_for_url(
                page=page, 
                url=url, 
                output_dir=OUTPUT_DIRECTORY,
                client=client,
                viewport=viewport_size
            )
            time.sleep(2)

        browser.close()

if __name__ == "__main__":
    main()
