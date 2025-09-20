refine_prompt = {
    'dom': '<{tag}{label}|{attr}{content}{subtree} >',
    'label': '[{label}]',
    'attr': '{attr}',
    'attr_splitter': '; ',
    'subtree_splitter': ' ',
}

xml_prompt = {
    'dom': '<{tag}{label}{attr}>{content}{subtree} </{tag}>',
    'label': ' id="{label}"',
    'attr': '{key}="{attr}"',
    'attr_splitter': ' ',
    'subtree_splitter': ' ',


}

refine_prompt_attr = {
    'dom': '<{tag}{label}|{attr}{content}{subtree} >',
    'label': '[{label}]',
    'attr': '{key}="{attr}"',  # ajout des attributs 
    'attr_splitter': '; ',
    'subtree_splitter': ' ',
}

prompts = {
    'refine': refine_prompt,
    'xml': xml_prompt,
    'new_data': refine_prompt, 
    'refine_attr':refine_prompt_attr
}

# prompts = {
#     'refine': refine_prompt,
#     'xml': xml_prompt,
#     'new_data': refine_prompt, 
# }
    