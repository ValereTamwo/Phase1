() => {
    // Supprime les marques précédentes pour éviter les doublons
    const prevElements = document.querySelectorAll('.possible-clickable-element');
    prevElements.forEach(el => el.classList.remove('possible-clickable-element'));

    // Sélectionne tous les éléments de la page
    const allElements = Array.from(document.querySelectorAll('*'));

    const clickableElements = allElements.filter(element => {
        const tagName = element.tagName.toLowerCase();
        
        // Critère 1: Est-ce que l'élément est désactivé ? Si oui, on l'ignore.
        if (element.disabled) {
            return false;
        }

        // Critère 2: L'élément a-t-il une taille minimale ? 
        // (On garde une vérification de taille très basique pour éviter les éléments de 0x0 pixels)
        // Note: offsetWidth/offsetHeight inclut les bordures et le padding, et fonctionne pour les éléments hors viewport.
        const hasSize = element.offsetWidth > 0 && element.offsetHeight > 0;
        if (!hasSize) {
            // Faisons une exception pour les balises <area> qui sont cliquables mais n'ont pas de taille propre.
            if (tagName !== 'area') {
                return false;
            }
        }

        let isClickable = false;

        // --- Début des heuristiques de "cliquabilité" (logique conservée) ---

        // Heuristique A: Écouteur d'événement 'onclick' ou curseur 'pointer'
        isClickable = (element.onclick != null) || window.getComputedStyle(element).cursor == "pointer";
        
        // Heuristique B: Attribut de rôle ARIA explicite
        if (!isClickable) {
            const role = element.getAttribute("role");
            const clickableRoles = [
                "button", "tab", "link", "checkbox", "menuitem", 
                "menuitemcheckbox", "menuitemradio", "radio"
            ];
            if (role && clickableRoles.includes(role.toLowerCase())) {
                isClickable = true;
            }
        }

        // Heuristique C: Balises HTML intrinsèquement interactives
        if (!isClickable) {
            const clickableTags = [
                "a", "button", "input", "textarea", "select", "details", 
                "iframe", "video", "object", "embed"
            ];
            if (clickableTags.includes(tagName)) {
                // Pour les inputs, on ignore les types cachés
                if (tagName === 'input' && element.type === 'hidden') {
                    isClickable = false;
                } else {
                    isClickable = true;
                }
            }
        }
        
        // Heuristique D: Attribut 'contentEditable'
        if (!isClickable) {
            const contentEditable = element.getAttribute("contentEditable");
            if (contentEditable != null && ["", "contenteditable", "true"].includes(contentEditable.toLowerCase())) {
                isClickable = true;
            }
        }

        // Heuristique E: Attribut 'jsaction' (spécifique à Google)
        if (!isClickable && element.hasAttribute("jsaction")) {
            isClickable = true; // Simplification : on suppose qu'un jsaction est souvent cliquable.
        }

        // Heuristique F: Cas particulier de <label> lié à un contrôle actif
        if (!isClickable && tagName === "label") {
            isClickable = (element.control != null) && !element.control.disabled;
        }

        // Heuristique G: Attribut 'tabindex' positif
        if (!isClickable) {
            const tabIndexValue = element.getAttribute("tabindex");
            if (tabIndexValue) {
                const tabIndex = parseInt(tabIndexValue, 10);
                if (!isNaN(tabIndex) && tabIndex >= 0) {
                    isClickable = true;
                }
            }
        }
        
        // Heuristique H: Nom de classe contenant "button" (moins fiable, gardée en dernier)
        if (!isClickable) {
            const className = element.getAttribute("class");
            if (className && className.toLowerCase().includes("button")) {
                isClickable = true;
            }
        }

        return isClickable;
    });

    // --- Filtrage final pour enlever les éléments parents ---
    // Si un élément cliquable contient un autre élément cliquable, on ne garde que l'enfant.
    // C'est la même logique que dans le script original pour éviter de marquer une carte entière ET le bouton à l'intérieur.
    const finalClickableElements = clickableElements.filter(x => 
        !clickableElements.some(y => x.contains(y) && x !== y)
    );

    // Applique la classe CSS finale aux éléments identifiés
    finalClickableElements.forEach(item => {
        item.classList.add('possible-clickable-element');
    });

    // Retourne le nombre d'éléments marqués pour information
    return finalClickableElements.length;
};