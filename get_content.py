from playwright.sync_api import CDPSession, Page, ViewportSize
from typing import Dict, Any
import lxml.html
import time

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

# Exemple d'utilisation
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        client = page.context.new_cdp_session(page)
        
        page.goto('https://en.wikipedia.org/wiki/Quantum_computing',timeout=60000)
        html = get_viewport_html(page, client, {"width": 1280, "height": 720})
        print("Contenu HTML du viewport:")
        print(html)
        
        client.detach()
        browser.close()

if __name__ == "__main__":
    main()